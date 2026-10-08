#!/usr/bin/env bash
# Publish a few days of normal history so trend questions have something to compare.
# The Dataflow job must be running (scripts/run_dataflow.sh). One batch per day, no incident:
# the incident (generator --incident-start) then stands out against these days.
# Usage: bash scripts/backfill.sh [first_day_ago] [last_day_ago] [events_per_day]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT=$(terraform -chdir="$ROOT/infra" output -raw project_id)
PYTHON="$ROOT/.venv/Scripts/python"
[[ -x "$PYTHON" ]] || PYTHON="$ROOT/.venv/bin/python"

FIRST=${1:-6}
LAST=${2:-2}
EVENTS=${3:-400}
RATE=1200  # events per minute: the publisher is synchronous, so this is a ceiling

for ((d = FIRST; d >= LAST; d--)); do
  echo "day -$d: $EVENTS events"
  "$PYTHON" "$ROOT/generator/publish.py" --project "$PROJECT" --rate "$RATE" \
    --duration "$(awk "BEGIN {print $EVENTS / $RATE}")" --event-days-ago "$d" --seed "$((200 + d))"
done
