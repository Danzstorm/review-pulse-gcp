# Datasets only. Tables (reviews_raw, reviews, products, reviews_enriched,
# review_embeddings) are created by the SQL scripts in sql/, not here.
# delete_contents_on_destroy: those tables live outside Terraform's state, so
# without it `terraform destroy` refuses to drop a non-empty dataset.
resource "google_bigquery_dataset" "bronze" {
  dataset_id = "bronze"
  project    = var.project_id
  location   = var.region

  delete_contents_on_destroy = true

  depends_on = [google_project_service.fase1]
}

# Landing table for the Dataflow pipeline. Unlike derived tables it lives here, not in
# sql/: the job cannot start without it, and `terraform apply` alone must make the stack
# runnable. The schema file is shared with the pipeline, so there is one definition.
resource "google_bigquery_table" "bronze_reviews_raw" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.bronze.dataset_id
  table_id            = "reviews_raw"
  schema              = file("${path.module}/../pipelines/dataflow/schemas/bronze_reviews_raw.json")
  deletion_protection = false # provider default is true, which would block `terraform destroy`

  time_partitioning {
    type  = "DAY"
    field = "ingest_ts"
  }
  require_partition_filter = true # every read must prune by ingest_ts; no accidental full scans
}

resource "google_bigquery_dataset" "silver" {
  dataset_id = "silver"
  project    = var.project_id
  location   = var.region

  delete_contents_on_destroy = true

  depends_on = [google_project_service.fase1]
}

resource "google_bigquery_dataset" "gold" {
  dataset_id = "gold"
  project    = var.project_id
  location   = var.region

  delete_contents_on_destroy = true

  depends_on = [google_project_service.fase1]
}
