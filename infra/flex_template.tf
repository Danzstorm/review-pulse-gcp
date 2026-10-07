# Flex Template: the pipeline ships as a container image in Artifact Registry, built by
# Cloud Build, so launching a job needs only gcloud (no local Python, Beam or Java).

locals {
  fase2_apis = [
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
  ]
}

resource "google_project_service" "fase2" {
  for_each = toset(local.fase2_apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = var.name_prefix
  format        = "DOCKER"

  depends_on = [google_project_service.fase2]
}

# Source tarballs for Cloud Build. Without an explicit bucket, `gcloud builds submit`
# creates <project>_cloudbuild on its own, which `terraform destroy` would never remove.
resource "google_storage_bucket" "builds" {
  name                        = "${var.project_id}-builds"
  location                    = var.region
  project                     = var.project_id
  force_destroy               = true
  uniform_bucket_level_access = true

  lifecycle_rule {
    condition {
      age = 7
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_service_account" "builder" {
  account_id   = "${var.name_prefix}-builder"
  display_name = "Review Pulse - Cloud Build"
  project      = var.project_id
}

resource "google_artifact_registry_repository_iam_member" "builder_pushes_images" {
  project    = var.project_id
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.builder.email}"
}

resource "google_storage_bucket_iam_member" "builder_reads_sources" {
  bucket = google_storage_bucket.builds.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.builder.email}"
}

# Build logs go to Cloud Logging only (cloudbuild.yaml), so no logs bucket is needed.
resource "google_project_iam_member" "builder_writes_logs" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.builder.email}"
}

# The Flex Template launcher VM runs as the job's service account and pulls the image.
resource "google_artifact_registry_repository_iam_member" "dataflow_pulls_images" {
  project    = var.project_id
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.dataflow_runner.email}"
}
