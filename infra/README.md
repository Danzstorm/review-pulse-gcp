# Infra — Fase 1

APIs, bucket, Pub/Sub, BigQuery datasets (bronze/silver/gold) and the
Dataflow runner service account. Vertex AI wiring and Cloud Run are added in
later phases, not here.

```bash
cp terraform.tfvars.example terraform.tfvars   # set your project_id
terraform init
terraform plan
terraform apply
```

`terraform destroy` must leave the project clean, so the stack can be torn
down between sessions at zero cost — nothing here has `prevent_destroy` or
retention locks.
