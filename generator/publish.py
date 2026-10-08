"""Publish synthetic product reviews to Pub/Sub.

Examples:
  python generator/publish.py --project my-proj --rate 20 --duration 10
  python generator/publish.py --project my-proj --incident-start 2 --incident-minutes 5
  python generator/publish.py --dry-run --rate 600 --duration 1 --seed 7
"""

import argparse
import csv
import json
import random
import sys
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

PRODUCTS_CSV = Path(__file__).with_name("products.csv")
FIELDS = {"review_id", "product_id", "customer_id", "rating", "title", "body", "channel", "event_ts"}
INCIDENT_SHARE = 0.5

# {topic: {"pos"|"neg": [[title, body], ...]}}, written by Gemini (scripts/build_bank.sh)
# and committed, so a run is still deterministic per --seed and needs no network.
# The generator never sends the topic: the enrichment step has to infer it.
TEMPLATES = json.loads(Path(__file__).with_name("review_bank.json").read_text(encoding="utf-8"))


def utc_ts(minutes_ago=0):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_ts(rng, days_ago):
    """A random moment of the UTC day `days_ago` days back (1h-23h, clear of midnight)."""
    midnight = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (midnight - timedelta(days=days_ago, seconds=-rng.randint(3600, 82800))).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_product_ids(path=PRODUCTS_CSV):
    with open(path, encoding="utf-8") as f:
        return [row["product_id"] for row in csv.DictReader(f)]


def make_event(rng, product_ids, *, product_id=None, topic=None, sentiment=None):
    topic = topic or rng.choice(sorted(TEMPLATES))
    sentiment = sentiment or rng.choices(["pos", "neg"], weights=[7, 3])[0]
    title, body = rng.choice(TEMPLATES[topic][sentiment])
    return {
        "review_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
        "product_id": product_id or rng.choice(product_ids),
        "customer_id": f"C-{rng.randint(1, 5000):04d}",
        "rating": rng.randint(4, 5) if sentiment == "pos" else rng.randint(1, 2),
        "title": title,
        "body": body,
        "channel": rng.choice(["app", "web", "email"]),
        "event_ts": utc_ts(),
    }


def corrupt(event, rng):
    """Break exactly one rule, so the dead-letter reason is unambiguous.

    Returns a dict, or a str for the malformed case (a message cut off mid-JSON).
    """
    bad = dict(event)
    rule = rng.choice(["malformed", "rating", "missing_field", "timestamp"])
    if rule == "malformed":
        return json.dumps(bad, ensure_ascii=False)[:-12]  # cuts into event_ts, the last key
    if rule == "rating":
        bad["rating"] = rng.choice([0, 6, 10, -1])
    elif rule == "missing_field":
        del bad[rng.choice(["review_id", "product_id", "body"])]
    else:
        bad["event_ts"] = "yesterday"
    return bad


def next_event(rng, product_ids, recent, elapsed_min, args):
    if recent and rng.random() < args.dup_ratio:
        return dict(rng.choice(recent))  # same review_id: what a producer retry looks like

    in_incident = (
        args.incident_start is not None
        and args.incident_start <= elapsed_min < args.incident_start + args.incident_minutes
    )
    if in_incident and rng.random() < INCIDENT_SHARE:
        event = make_event(rng, product_ids, product_id=args.incident_product, topic="conectividad", sentiment="neg")
    else:
        event = make_event(rng, product_ids)

    # Late arrival: event time hours behind ingest time, like a phone that was offline.
    if rng.random() < args.late_ratio:
        event["event_ts"] = utc_ts(minutes_ago=rng.randint(30, 360))

    if args.event_days_ago:  # backfill: the review was written on an earlier day
        event["event_ts"] = day_ts(rng, args.event_days_ago)

    if rng.random() < args.invalid_ratio:
        return corrupt(event, rng)
    recent.append(event)
    return event


def encode(event):
    text = event if isinstance(event, str) else json.dumps(event, ensure_ascii=False)
    return text.encode("utf-8")


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", help="GCP project id (required unless --dry-run)")
    p.add_argument("--topic", default="reviews")
    p.add_argument("--rate", type=float, default=20, help="events per minute")
    p.add_argument("--duration", type=float, default=10, help="minutes")
    p.add_argument("--invalid-ratio", type=float, default=0.04)
    p.add_argument("--dup-ratio", type=float, default=0.02)
    p.add_argument("--late-ratio", type=float, default=0.05)
    p.add_argument("--incident-product", default="P-0001")
    p.add_argument("--incident-start", type=float, help="minute the incident starts; omit for no incident")
    p.add_argument("--incident-minutes", type=float, default=5)
    p.add_argument("--event-days-ago", type=int, default=0,
                   help="backfill: stamp event_ts on that many days back (history for trend questions)")
    p.add_argument("--seed", type=int)
    p.add_argument("--dry-run", action="store_true", help="print events instead of publishing")
    args = p.parse_args(argv)
    if not args.dry_run and not args.project:
        p.error("--project is required unless --dry-run")
    return args


def main(argv=None):
    args = parse_args(argv)
    rng = random.Random(args.seed)
    product_ids = load_product_ids()
    recent = deque(maxlen=50)

    if args.dry_run:
        send = lambda data: sys.stdout.buffer.write(data + b"\n")
    else:
        from google.cloud import pubsub_v1  # lazy: --dry-run works without the SDK

        client = pubsub_v1.PublisherClient()
        topic_path = client.topic_path(args.project, args.topic)
        # ponytail: synchronous publish, fine at demo rates (~20/min); batch futures if rate grows to thousands/min
        send = lambda data: client.publish(topic_path, data).result()

    total = int(args.rate * args.duration)
    for i in range(total):
        event = next_event(rng, product_ids, recent, elapsed_min=i / args.rate, args=args)
        send(encode(event))
        if not args.dry_run:
            time.sleep(60 / args.rate)


if __name__ == "__main__":
    main()
