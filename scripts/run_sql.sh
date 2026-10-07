#!/usr/bin/env bash
# Run a SQL file from sql/ in BigQuery. ${project} and ${lookback_days} are filled in
# from `terraform output` and the LOOKBACK_DAYS env var (default 3).
# Usage: bash scripts/run_sql.sh sql/silver/reviews.sql
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT=$(terraform -chdir="$ROOT/infra" output -raw project_id)
REGION=$(terraform -chdir="$ROOT/infra" output -raw region)

sql=$(<"$ROOT/$1")
sql=${sql//'${project}'/$PROJECT}
sql=${sql//'${lookback_days}'/${LOOKBACK_DAYS:-3}}
bq query --project_id="$PROJECT" --location="$REGION" --use_legacy_sql=false --nouse_cache <<<"$sql"
