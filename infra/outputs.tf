output "project_id" {
  value = var.project_id
}

output "region" {
  value = var.region
}

output "bronze_table" {
  description = "Beam table spec (project:dataset.table) for the pipeline's --bronze_table."
  value       = "${var.project_id}:${google_bigquery_dataset.bronze.dataset_id}.${google_bigquery_table.bronze_reviews_raw.table_id}"
}

output "bucket_name" {
  value = google_storage_bucket.review_pulse.name
}

output "pubsub_topic" {
  value = google_pubsub_topic.reviews.name
}

output "pubsub_subscription" {
  value = google_pubsub_subscription.reviews_dataflow.name
}

output "bigquery_datasets" {
  value = {
    bronze = google_bigquery_dataset.bronze.dataset_id
    silver = google_bigquery_dataset.silver.dataset_id
    gold   = google_bigquery_dataset.gold.dataset_id
  }
}

output "dataflow_runner_email" {
  value = google_service_account.dataflow_runner.email
}
