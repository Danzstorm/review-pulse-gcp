import json
import random
import sys
import tempfile
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "generator"))
sys.path.insert(0, str(ROOT / "pipelines" / "dataflow"))

import apache_beam as beam  # noqa: E402
from google.api_core.datetime_helpers import DatetimeWithNanoseconds  # noqa: E402
from apache_beam.io.gcp.pubsub import PubsubMessage  # noqa: E402
from apache_beam.testing.test_pipeline import TestPipeline  # noqa: E402
from apache_beam.testing.util import assert_that, equal_to  # noqa: E402
from apache_beam.transforms.window import IntervalWindow, TimestampedValue  # noqa: E402
from apache_beam.utils.timestamp import Timestamp  # noqa: E402

import pipeline  # noqa: E402
import publish  # noqa: E402

NOW = datetime(2026, 9, 23, 17, 5, tzinfo=timezone.utc)


def check(payload):
    return pipeline.validate(payload if isinstance(payload, bytes) else json.dumps(payload).encode(), "m-1", NOW)


def good(**overrides):
    event = {
        "review_id": "r-1", "product_id": "P-0001", "customer_id": "C-0001", "rating": 2,
        "title": "Se desconectan", "body": "Pierde conexión.", "channel": "app", "event_ts": "2026-09-23T14:32:10Z",
    }
    event.update(overrides)
    return event


def test_contract_with_generator():
    """Every event the generator emits lands where it should, for the reason it should."""
    args = publish.parse_args(["--dry-run"])
    args.invalid_ratio, args.dup_ratio, args.late_ratio = 0.2, 0.05, 0.05
    rng, recent, ids = random.Random(7), deque(maxlen=50), publish.load_product_ids()

    def expected(event):
        if isinstance(event, str):
            return "malformed_json"
        missing = [f for f in pipeline.REQUIRED_FIELDS if f not in event]
        if missing:
            return "missing_fields:" + ",".join(missing)
        if not 1 <= event["rating"] <= 5:
            return "invalid_rating"
        if event["event_ts"] == "yesterday":
            return "invalid_event_ts"
        return "valid"

    outcomes = Counter()
    for i in range(3000):
        event = publish.next_event(rng, ids, recent, i / args.rate, args)
        tag, record = pipeline.validate(publish.encode(event), f"m-{i}", NOW)
        got = "valid" if tag == "valid" else record["reason"]
        assert got == expected(event), (got, event)
        outcomes[got.split(":")[0]] += 1

    assert set(outcomes) == {"valid", "malformed_json", "missing_fields", "invalid_rating", "invalid_event_ts"}, outcomes


def test_valid_row_normalizes_timestamps_to_utc():
    tag, row = check(good(event_ts="2026-09-23T09:32:10-05:00"))
    assert tag == "valid"
    assert row["event_ts"] == datetime(2026, 9, 23, 14, 32, 10, tzinfo=timezone.utc)
    assert row["ingest_ts"] == NOW and row["message_id"] == "m-1"


def test_edge_cases_are_rejected_with_one_reason():
    cases = {
        b"\xff\xfe not utf-8": "malformed_json",
        b"[1, 2]": "not_an_object",
        json.dumps(good(rating=True)).encode(): "invalid_rating",  # bool is an int subclass in Python
        json.dumps(good(rating="5")).encode(): "invalid_rating",
        json.dumps(good(title=42)).encode(): "invalid_type",
        json.dumps(good(event_ts="2026-09-23T14:32:10")).encode(): "invalid_event_ts",  # naive
        json.dumps(good(body="")).encode(): "missing_fields:body",
    }
    for payload, reason in cases.items():
        tag, record = check(payload)
        assert (tag, record["reason"]) == ("invalid", reason), (payload, record)


def test_parse_and_validate_routes_outputs_in_beam():
    # The exact type Dataflow delivers for publish_time (a datetime subclass, not a Beam Timestamp).
    publish_time = DatetimeWithNanoseconds(2026, 9, 23, 17, 5, tzinfo=timezone.utc)
    messages = [
        PubsubMessage(json.dumps(good(review_id="a")).encode(), None, message_id="1", publish_time=publish_time),
        PubsubMessage(b'{"review_id": "b"', None, message_id="2", publish_time=publish_time),
        PubsubMessage(json.dumps(good(review_id="c", rating=9)).encode(), None, message_id="3", publish_time=publish_time),
    ]
    with TestPipeline() as p:
        out = p | beam.Create(messages) | beam.ParDo(pipeline.ParseAndValidate()).with_outputs("invalid", main="valid")
        assert_that(
            out.valid | "ValidIds" >> beam.Map(lambda r: (r["review_id"], r["ingest_ts"], type(r["ingest_ts"]))),
            equal_to([("a", NOW, datetime)]),
            label="valid",
        )
        assert_that(
            out.invalid | "Reasons" >> beam.Map(lambda r: (r["message_id"], r["reason"])),
            equal_to([("2", "malformed_json"), ("3", "invalid_rating")]),
            label="invalid",
        )


def test_rows_survive_the_bigquery_conversion_step():
    """Runs the exact transform that failed on Dataflow ('Convert dict to Beam Row'), without BigQuery."""
    from apache_beam.io.gcp.bigquery import StorageWriteToBigQuery

    schema = {"fields": json.loads(pipeline.BRONZE_SCHEMA_PATH.read_text(encoding="utf-8"))}
    _, row = check(good())
    with TestPipeline() as p:
        rows = (
            p
            | beam.Create([pipeline.to_bq_row(row)])
            | StorageWriteToBigQuery.ConvertToBeamRows(schema, False).with_output_types()
        )
        assert_that(
            rows | beam.Map(lambda r: (r.review_id, r.rating, r.ingest_ts.to_utc_datetime(has_tz=True))),
            equal_to([("r-1", 2, NOW)]),
        )


def test_one_file_per_window_and_no_lost_lines():
    """Regression: fileio wrote one file per bundle and same-window files overwrote each other."""
    start = Timestamp.from_utc_datetime(datetime(2026, 9, 23, 23, 55, tzinfo=timezone.utc))
    same_window = [TimestampedValue({"review_id": f"w1-{i}", "ingest_ts": NOW}, start + i) for i in range(25)]
    next_window = [TimestampedValue({"review_id": "w2-0", "ingest_ts": NOW}, start + 360)]
    with tempfile.TemporaryDirectory() as tmp:
        with TestPipeline() as p:
            pipeline.write_windowed_jsonl(p | beam.Create(same_window + next_window), "Test", tmp, "reviews")

        files = sorted(str(f.relative_to(tmp)).replace("\\", "/") for f in Path(tmp).rglob("*.jsonl"))
        assert files == ["dt=2026-09-23/reviews-2355-p0.jsonl", "dt=2026-09-24/reviews-0000-p0.jsonl"], files
        lines = [json.loads(l) for l in (Path(tmp) / files[0]).read_text(encoding="utf-8").splitlines()]
        assert sorted(r["review_id"] for r in lines) == sorted(f"w1-{i}" for i in range(25))
        assert lines[0]["ingest_ts"].startswith("2026-09-23T17:05")


def test_file_name_is_a_pure_function_of_window_and_pane():
    window = IntervalWindow(Timestamp.from_utc_datetime(NOW), Timestamp.from_utc_datetime(NOW) + 300)
    assert pipeline.window_file_name("rejected", window, 0) == "dt=2026-09-23/rejected-1705-p0.jsonl"
    assert pipeline.window_file_name("rejected", window, 1) == "dt=2026-09-23/rejected-1705-p1.jsonl"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
