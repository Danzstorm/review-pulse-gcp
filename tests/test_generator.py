import json
import random
import sys
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generator"))
import publish  # noqa: E402

IDS = publish.load_product_ids()


def run(n, **overrides):
    args = publish.parse_args(["--dry-run"])
    for key, value in overrides.items():
        setattr(args, key, value)
    rng, recent = random.Random(42), deque(maxlen=50)
    return [publish.next_event(rng, IDS, recent, i / args.rate, args) for i in range(n)]


def is_valid(e):
    if isinstance(e, str) or set(e) != publish.FIELDS or not 1 <= e["rating"] <= 5:
        return False
    try:
        datetime.strptime(e["event_ts"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return False
    return True


def test_invalid_ratio_is_respected():
    events = run(5000, invalid_ratio=0.04, dup_ratio=0)
    share = sum(not is_valid(e) for e in events) / len(events)
    assert 0.03 <= share <= 0.05, share


def test_duplicates_reuse_review_id():
    events = run(2000, invalid_ratio=0, dup_ratio=0.05)
    ids = [e["review_id"] for e in events]
    assert len(ids) - len(set(ids)) > 50


def test_incident_concentrates_negative_connectivity_reviews():
    # rate 20/min: minute 0-5 normal, minute 5-10 incident on P-0001
    events = run(200, invalid_ratio=0, dup_ratio=0, incident_start=5, incident_minutes=5)
    before, during = events[:100], events[100:]
    hits = lambda batch: sum(e["product_id"] == "P-0001" and e["rating"] <= 2 for e in batch)
    assert hits(during) >= 35, hits(during)
    assert hits(before) <= 10, hits(before)


def test_malformed_payloads_do_not_parse():
    events = run(3000, invalid_ratio=0.2, dup_ratio=0)
    raw = [e for e in events if isinstance(e, str)]
    assert len(raw) > 50, len(raw)
    for text in raw:
        try:
            json.loads(text)
        except json.JSONDecodeError:
            continue
        raise AssertionError(f"parsed but should not: {text}")


def test_late_events_lag_behind_ingest_time():
    events = run(3000, invalid_ratio=0, dup_ratio=0, late_ratio=0.05)
    now = datetime.now(timezone.utc)
    age = lambda e: now - datetime.strptime(e["event_ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    share = sum(age(e) >= timedelta(minutes=29) for e in events) / len(events)
    assert 0.03 <= share <= 0.07, share


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
