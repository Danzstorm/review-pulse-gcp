"""Read build_bank.sql rows (JSON on stdin) and write the bank: {topic: {sentiment: [[title, body], ...]}}."""
import json
import sys
from collections import defaultdict

bank, seen = defaultdict(lambda: defaultdict(list)), set()
for row in json.load(sys.stdin):
    title, body = row["title"].strip(), row["body"].strip()
    key = (title.lower(), body.lower())
    if title and body and key not in seen:
        seen.add(key)
        bank[row["topic"]][row["sentiment"]].append([title, body])

with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(bank, f, ensure_ascii=False, indent=1, sort_keys=True)
print({t: {s: len(v) for s, v in d.items()} for t, d in sorted(bank.items())})
