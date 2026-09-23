#!/usr/bin/env bash
# Tear everything down to zero cost. Jobs first: Terraform does not manage the running
# Dataflow job, so `terraform destroy` alone would leave it billing.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
"$ROOT/scripts/stop.sh" --cancel
terraform -chdir="$ROOT/infra" destroy
