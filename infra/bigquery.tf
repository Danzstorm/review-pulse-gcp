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
