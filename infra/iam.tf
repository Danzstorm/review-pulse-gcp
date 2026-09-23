# Service account for the Fase 2 Dataflow pipeline. Bound only to what it
# needs, and resource-scoped (not project-wide) wherever the API allows it —
# matches the spec's own "permisos minimos" principle (section 4.6).
resource "google_service_account" "dataflow_runner" {
  account_id   = "${var.name_prefix}-dataflow-runner"
  display_name = "Review Pulse - Dataflow runner"
  project      = var.project_id
}

# dataflow.worker has no resource-scoped equivalent: Dataflow requires it at
# project level for workers to report status and manage the job.
resource "google_project_iam_member" "dataflow_worker" {
  project = var.project_id
  role    = "roles/dataflow.worker"
  member  = "serviceAccount:${google_service_account.dataflow_runner.email}"
}

resource "google_pubsub_subscription_iam_member" "dataflow_subscriber" {
  project      = var.project_id
  subscription = google_pubsub_subscription.reviews_dataflow.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.dataflow_runner.email}"
}

# subscriber can consume but not read the subscription's config (pubsub.subscriptions.get),
# which Dataflow queries at startup to check ack deadline and unsupported settings.
resource "google_pubsub_subscription_iam_member" "dataflow_subscription_viewer" {
  project      = var.project_id
  subscription = google_pubsub_subscription.reviews_dataflow.name
  role         = "roles/pubsub.viewer"
  member       = "serviceAccount:${google_service_account.dataflow_runner.email}"
}

resource "google_storage_bucket_iam_member" "dataflow_bucket_writer" {
  bucket = google_storage_bucket.review_pulse.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.dataflow_runner.email}"
}

resource "google_bigquery_dataset_iam_member" "dataflow_bronze_writer" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.bronze.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.dataflow_runner.email}"
}

# bigquery.jobUser has no dataset-scoped equivalent: running a load/write job
# (Storage Write API) always requires project-level job creation rights.
resource "google_project_iam_member" "dataflow_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.dataflow_runner.email}"
}
