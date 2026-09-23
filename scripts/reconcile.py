"""Reconcile one generator run against every sink. Exits 1 on any mismatch.

The generator is deterministic, so the expected outcome of a run is known before it
happens. This replays the same generator arguments locally through the pipeline's own
validate(), then compares against three independent measurements:
  1. Dataflow user counters (what the pipeline classified)
  2. rows in bronze.reviews_raw + lines in raw/ (what landed, valid side)
  3. lines in dead-letter/ grouped by reason (what landed, rejected side)

Usage (everything after `--` is passed to generator/publish.py):
  python scripts/reconcile.py --since 2026-09-23T18:50:00Z --job <JOB_ID> -- \
      --rate 60 --duration 5 --incident-start 1 --incident-minutes 3 --seed 31

Counters are per job: run one generator batch per job for them to be comparable.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "generator"), str(ROOT / "pipelines" / "dataflow")]

import pipeline  # noqa: E402
import publish  # noqa: E402

OUTCOMES = ("valid", "malformed_json", "not_an_object", "missing_fields", "invalid_rating", "invalid_type", "invalid_event_ts")


def run(*cmd):
    exe = shutil.which(cmd[0]) or cmd[0]  # resolves gcloud.cmd / terraform.exe on Windows
    return subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, check=True).stdout


def outcome(tag, record):
    return "valid" if tag == "valid" else record["reason"].split(":")[0]


def expected_counts(generator_args):
    args = publish.parse_args(["--dry-run", *generator_args])
    rng, recent, ids = publish.random.Random(args.seed), deque(maxlen=50), publish.load_product_ids()
    now = datetime.now(timezone.utc)
    counts = Counter()
    for i in range(int(args.rate * args.duration)):
        event = publish.next_event(rng, ids, recent, i / args.rate, args)
        counts[outcome(*pipeline.validate(publish.encode(event), "replay", now))] += 1
    return counts


def dataflow_counters(project, region, job):
    raw = run("gcloud", "beta", "dataflow", "metrics", "list", job, f"--project={project}",
              f"--region={region}", "--source=user", "--format=json")
    counts = Counter()
    for metric in json.loads(raw):
        name = metric["name"]["name"]
        if name in OUTCOMES and not metric["name"].get("context", {}).get("tentative"):
            counts[name] = int(metric["scalar"])
    return counts


def gcs_records(bucket, prefix, since):
    # ponytail: reads every file under the prefix; filter by dt= partitions if history grows
    for blob in bucket.list_blobs(prefix=prefix):
        for line in blob.download_as_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            if datetime.fromisoformat(record["ingest_ts"]) >= since:
                yield record


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", required=True, help="UTC ISO timestamp just before publishing started")
    parser.add_argument("--job", required=True, help="Dataflow job id")
    parser.add_argument("--bq-subscription", action="store_true",
                        help="also compare the ELT experiment (bronze.reviews_bqsub_classified)")
    parser.add_argument("generator_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
    generator_args = [a for a in args.generator_args if a != "--"]

    tf = json.loads(run("terraform", f"-chdir={ROOT / 'infra'}", "output", "-json"))
    project, region, bucket_name = (tf[k]["value"] for k in ("project_id", "region", "bucket_name"))
    os.environ.setdefault("GOOGLE_CLOUD_QUOTA_PROJECT", project)

    from google.cloud import bigquery, storage  # installed with apache-beam[gcp]

    expected = expected_counts(generator_args)
    counters = dataflow_counters(project, region, args.job)

    bq = bigquery.Client(project=project)
    query = "SELECT COUNT(*) AS n FROM bronze.reviews_raw WHERE ingest_ts >= @since"
    config = bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("since", "TIMESTAMP", since)])
    bronze_rows = next(iter(bq.query(query, job_config=config).result())).n

    bucket = storage.Client(project=project).bucket(bucket_name)
    raw_lines = sum(1 for _ in gcs_records(bucket, "raw/reviews/", since))
    dead_letter = Counter(r["reason"].split(":")[0] for r in gcs_records(bucket, "dead-letter/", since))

    columns = {"expected": expected, "counters": counters, "landed": Counter(dead_letter, valid=bronze_rows)}
    if args.bq_subscription:
        elt = ("SELECT SPLIT(outcome, ':')[OFFSET(0)] AS outcome, COUNT(*) AS n "
               "FROM bronze.reviews_bqsub_classified WHERE ingest_ts >= @since GROUP BY 1")
        columns["bq_sub_sql"] = Counter({r.outcome: r.n for r in bq.query(elt, job_config=config).result()})

    ok = True
    print(f"{'outcome':<18}" + "".join(f"{c:>12}" for c in columns))
    for name in OUTCOMES:
        row = [col[name] for col in columns.values()]
        if any(row):
            mark = "" if len(set(row)) == 1 else "  <-- MISMATCH"
            ok &= not mark
            print(f"{name:<18}" + "".join(f"{v:>12}" for v in row) + mark)
    raw_mark = "" if raw_lines == bronze_rows else "  <-- MISMATCH"
    ok &= not raw_mark
    print(f"\nraw/ lines {raw_lines} vs bronze rows {bronze_rows}{raw_mark}")
    print("RECONCILED" if ok else "NOT RECONCILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
