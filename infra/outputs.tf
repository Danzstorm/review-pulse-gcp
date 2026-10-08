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

output "template_image" {
  value = "${google_artifact_registry_repository.images.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/pipeline"
}

output "builds_bucket" {
  value = google_storage_bucket.builds.name
}

output "builder_email" {
  value = google_service_account.builder.email
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

output "vertex_connection" {
  description = "BigQuery connection used by the remote Gemini and embedding models."
  value       = "${var.project_id}.${var.region}.${google_bigquery_connection.vertex.connection_id}"
}

output "agent_image" {
  description = "Image path (without tag) of the agent in Artifact Registry."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/agent"
}

output "agent_url" {
  description = "Cloud Run URL of the agent; null until deploy_agent = true."
  value       = var.deploy_agent ? google_cloud_run_v2_service.agent[0].uri : null
}
