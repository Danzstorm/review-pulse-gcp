#!/usr/bin/env bash
# Run a SQL file from sql/ in BigQuery. ${project} and ${region} come from
# `terraform output`; ${lookback_days} and ${gemini_endpoint} from the LOOKBACK_DAYS and
# GEMINI_ENDPOINT env vars (defaults: 3, gemini-2.5-flash).
# Usage: bash scripts/run_sql.sh sql/silver/reviews.sql
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT=$(terraform -chdir="$ROOT/infra" output -raw project_id)
REGION=$(terraform -chdir="$ROOT/infra" output -raw region)

sql=$(<"$ROOT/$1")
sql=${sql//'${project}'/$PROJECT}
sql=${sql//'${region}'/$REGION}
sql=${sql//'${gemini_endpoint}'/${GEMINI_ENDPOINT:-gemini-2.5-flash}}
sql=${sql//'${lookback_days}'/${LOOKBACK_DAYS:-3}}
sql=${sql//'${enrich_limit}'/${ENRICH_LIMIT:-200}}
bq query --project_id="$PROJECT" --location="$REGION" --use_legacy_sql=false --nouse_cache <<<"$sql"
