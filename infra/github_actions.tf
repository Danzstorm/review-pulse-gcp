# Fase 5: GitHub Actions deploys to GCP without any stored key (Workload Identity Federation).
# GitHub signs a short-lived token for each workflow run; GCP checks who signed it and what
# repository and branch it came from, and swaps it for credentials that last minutes.

variable "github_repo" {
  description = "GitHub repository (owner/name) allowed to deploy."
  type        = string
  default     = "Danzstorm/review-pulse-gcp"
}

resource "google_project_service" "fase5" {
  for_each = toset(["iam.googleapis.com", "iamcredentials.googleapis.com", "sts.googleapis.com"])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "${var.name_prefix}-github"
  display_name              = "GitHub Actions"

  depends_on = [google_project_service.fase5]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  # Without this condition, ANY GitHub repository could ask for these credentials.
  # Only this repository, only from main: a fork or a pull request cannot deploy.
  attribute_condition = "assertion.repository == \"${var.github_repo}\" && assertion.ref == \"refs/heads/main\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "deployer" {
  account_id   = "${var.name_prefix}-deployer"
  display_name = "Review Pulse - GitHub Actions deployer"
  project      = var.project_id
}

resource "google_service_account_iam_member" "github_impersonates_deployer" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}

# What deploying the agent needs, and nothing else. The deployer builds the image (as the
# builder account) and rolls a new revision of the service (as the agent account).
resource "google_project_iam_member" "deployer" {
  # Project level because no narrower scope exists for these, or the target may not exist yet
  # (the Cloud Run service is created conditionally).
  for_each = toset([
    "roles/cloudbuild.builds.editor",          # start builds
    "roles/run.developer",                     # update the service
    "roles/logging.viewer",                    # stream the build log
    "roles/serviceusage.serviceUsageConsumer", # gcloud calls attributed to this project
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_acts_as_builder" {
  service_account_id = google_service_account.builder.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_service_account_iam_member" "deployer_acts_as_agent" {
  service_account_id = google_service_account.agent.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_storage_bucket_iam_member" "deployer_uploads_sources" {
  bucket = google_storage_bucket.builds.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.deployer.email}"
}
