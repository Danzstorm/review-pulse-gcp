"""Streaming pipeline: Pub/Sub -> validate -> BigQuery bronze + GCS raw; rejects -> GCS dead-letter.

Deduplication is deliberately NOT done here: bronze is append-only and silver's
MERGE is the single place where idempotency is enforced.

Launch on Dataflow with scripts/run_dataflow.sh (reads every option from `terraform output`).
No local runner executes the full graph: the Python DirectRunner rejects the cross-language
BigQuery sink in streaming, and PrismRunner does not support the native Pub/Sub source.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import apache_beam as beam
from apache_beam.io.filesystems import FileSystems
from apache_beam.metrics import Metrics
from apache_beam.options.pipeline_options import PipelineOptions, SetupOptions, StandardOptions
from apache_beam.transforms.window import FixedWindows
from apache_beam.utils.timestamp import Timestamp

REQUIRED_FIELDS = ("review_id", "product_id", "customer_id", "rating", "title", "body", "channel", "event_ts")
WINDOW_SECONDS = 300
# Shared with infra/bigquery.tf, which creates the table from the same file.
BRONZE_SCHEMA_PATH = Path(__file__).parent / "schemas" / "bronze_reviews_raw.json"


def validate(data, message_id, ingest_ts):
    """Classify one Pub/Sub payload. Pure: no Beam, no I/O.

    Returns ("valid", row) or ("invalid", dead_letter_record). Each rejection
    carries exactly one reason so the dead-letter is queryable by cause.
    """

    def reject(reason):
        return "invalid", {
            "reason": reason,
            "payload": data.decode("utf-8", errors="replace"),
            "message_id": message_id,
            "ingest_ts": ingest_ts,
        }

    try:
        event = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return reject("malformed_json")
    if not isinstance(event, dict):
        return reject("not_an_object")

    missing = [f for f in REQUIRED_FIELDS if event.get(f) in (None, "")]
    if missing:
        return reject("missing_fields:" + ",".join(missing))

    rating = event["rating"]
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        return reject("invalid_rating")

    if not all(isinstance(event[f], str) for f in REQUIRED_FIELDS if f != "rating"):
        return reject("invalid_type")

    try:
        event_ts = datetime.fromisoformat(event["event_ts"])
    except ValueError:
        return reject("invalid_event_ts")
    if event_ts.tzinfo is None:
        return reject("invalid_event_ts")  # naive timestamps are ambiguous; producers must send UTC

    row = {f: event[f] for f in REQUIRED_FIELDS}
    row["event_ts"] = event_ts.astimezone(timezone.utc)
    row["ingest_ts"] = ingest_ts
    row["message_id"] = message_id
    return "valid", row


class ParseAndValidate(beam.DoFn):
    def process(self, message):
        # Publish time, not wall clock: a redelivered message keeps the same ingest_ts.
        # It arrives as a datetime subclass (DatetimeWithNanoseconds); normalize to a plain UTC datetime.
        published = message.publish_time
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        ingest_ts = datetime.fromtimestamp(published.timestamp(), tz=timezone.utc)
        tag, record = validate(message.data, message.message_id, ingest_ts)
        # Per-outcome counters (Dataflow UI / monitoring): reconcile them against what the sinks hold.
        Metrics.counter("validation", "valid" if tag == "valid" else record["reason"].split(":")[0]).inc()
        if tag == "valid":
            yield record
        else:
            yield beam.pvalue.TaggedOutput("invalid", record)


def to_bq_row(row):
    """The Storage Write API path converts dicts to Beam Rows, whose TIMESTAMP coder needs Beam Timestamps."""
    return {
        **row,
        "event_ts": Timestamp.from_utc_datetime(row["event_ts"]),
        "ingest_ts": Timestamp.from_utc_datetime(row["ingest_ts"]),
    }


def to_json_line(record):
    return json.dumps(record, ensure_ascii=False, default=lambda ts: ts.isoformat())


def window_file_name(prefix, window, pane_index):
    """dt=YYYY-MM-DD/<prefix>-HHMM-p<pane>.jsonl, dated by window start (≈ ingest time)."""
    start = window.start.to_utc_datetime(has_tz=True)
    return f"dt={start:%Y-%m-%d}/{prefix}-{start:%H%M}-p{pane_index}.jsonl"


class WriteWindowFile(beam.DoFn):
    """Writes one grouped window firing as one JSONL file.

    Replaces fileio.WriteToFiles, which in streaming wrote one file per bundle and
    numbered shards per move group, so two groups of the same window got the same
    name and overwrote each other. Here the name is a pure function of (window, pane):
    a GroupByKey emits each pane once, and a retried bundle rewrites its own file.
    """

    def __init__(self, path, prefix):
        self.path = path
        self.prefix = prefix

    def process(self, keyed_lines, window=beam.DoFn.WindowParam, pane=beam.DoFn.PaneInfoParam):
        _, lines = keyed_lines
        target = FileSystems.join(self.path, window_file_name(self.prefix, window, pane.index))
        with FileSystems.create(target) as f:
            f.write("".join(line + "\n" for line in lines).encode("utf-8"))
        yield target


def write_windowed_jsonl(pcoll, label, path, prefix):
    return (
        pcoll
        | f"{label}ToJson" >> beam.Map(to_json_line)
        | f"{label}Window" >> beam.WindowInto(FixedWindows(WINDOW_SECONDS))
        # ponytail: one key funnels each window through one worker; shard the key if volume grows
        | f"{label}Key" >> beam.WithKeys(0)
        | f"{label}Group" >> beam.GroupByKey()
        | f"{label}Write" >> beam.ParDo(WriteWindowFile(path, prefix))
    )


class ReviewOptions(PipelineOptions):
    @classmethod
    def _add_argparse_args(cls, parser):
        parser.add_argument("--input_subscription", help="projects/<p>/subscriptions/<s>")
        parser.add_argument("--bronze_table", help="<project>:bronze.reviews_raw")
        parser.add_argument("--bucket", help="bucket name, without gs://")


def run(argv=None):
    options = PipelineOptions(argv)
    options.view_as(StandardOptions).streaming = True
    options.view_as(SetupOptions).save_main_session = True
    opts = options.view_as(ReviewOptions)
    missing = [n for n in ("input_subscription", "bronze_table", "bucket") if not getattr(opts, n)]
    if missing:
        raise SystemExit(f"missing required options: {', '.join('--' + n for n in missing)}")

    p = beam.Pipeline(options=options)
    results = (
        p
        | "ReadPubSub" >> beam.io.ReadFromPubSub(subscription=opts.input_subscription, with_attributes=True)
        | "ParseValidate" >> beam.ParDo(ParseAndValidate()).with_outputs("invalid", main="valid")
    )

    # At-least-once is enough: silver's MERGE deduplicates, and it skips exactly-once's shuffle cost.
    # Storage Write API is a Java cross-language transform: pipeline construction needs a JRE.
    results.valid | "ToBqRow" >> beam.Map(to_bq_row) | "WriteBronze" >> beam.io.WriteToBigQuery(
        opts.bronze_table,
        schema={"fields": json.loads(BRONZE_SCHEMA_PATH.read_text(encoding="utf-8"))},
        method=beam.io.WriteToBigQuery.Method.STORAGE_WRITE_API,
        use_at_least_once=True,
        create_disposition=beam.io.BigQueryDisposition.CREATE_NEVER,
        write_disposition=beam.io.BigQueryDisposition.WRITE_APPEND,
    )
    write_windowed_jsonl(results.valid, "Raw", f"gs://{opts.bucket}/raw/reviews", "reviews")
    write_windowed_jsonl(results.invalid, "DeadLetter", f"gs://{opts.bucket}/dead-letter", "rejected")

    result = p.run()
    # A streaming job never finishes: on Dataflow, submit and return. Local runners must block.
    if "Dataflow" not in str(options.view_as(StandardOptions).runner):
        result.wait_until_finish()


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    run()
