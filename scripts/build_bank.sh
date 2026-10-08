#!/usr/bin/env bash
# Rebuild generator/review_bank.json from Gemini (through the gold.gemini remote model).
# Needs the Fase 3 infra applied. Dedupes by title+body and groups by topic and sentiment.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT=$(terraform -chdir="$ROOT/infra" output -raw project_id)
REGION=$(terraform -chdir="$ROOT/infra" output -raw region)

sql=$(<"$ROOT/generator/build_bank.sql")
sql=${sql//'${project}'/$PROJECT}
bq query --project_id="$PROJECT" --location="$REGION" --use_legacy_sql=false --nouse_cache \
  --max_rows=1000 --format=json <<<"$sql" | python "$ROOT/generator/write_bank.py" "$ROOT/generator/review_bank.json"
