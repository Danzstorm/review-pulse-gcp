# Fase 4: the agent as a Cloud Run service. Identity first, service second.
# The service is created only when var.deploy_agent is true, because Cloud Run needs the
# image to exist: apply once, run scripts/deploy_agent.sh, then set deploy_agent = true.

variable "deploy_agent" {
  description = "Create the Cloud Run service. Needs the image built by scripts/deploy_agent.sh."
  type        = bool
  default     = false
}

resource "google_project_service" "fase4" {
  for_each = toset(["run.googleapis.com"])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "agent" {
  account_id   = "${var.name_prefix}-agent"
  display_name = "Review Pulse - agent (Cloud Run)"
  project      = var.project_id
}

# Reads only gold. The agent never touches bronze or silver.
resource "google_bigquery_dataset_iam_member" "agent_reads_gold" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.gold.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.agent.email}"
}

# jobUser has no dataset-scoped equivalent: any query needs project-level job creation.
resource "google_project_iam_member" "agent_bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.agent.email}"
}

# ML.GENERATE_EMBEDDING inside the search query goes through the remote model's connection.
resource "google_bigquery_connection_iam_member" "agent_uses_connection" {
  project       = var.project_id
  location      = google_bigquery_connection.vertex.location
  connection_id = google_bigquery_connection.vertex.connection_id
  role          = "roles/bigquery.connectionUser"
  member        = "serviceAccount:${google_service_account.agent.email}"
}

# The agent itself calls Gemini on Vertex AI (function calling runs in the service).
resource "google_project_iam_member" "agent_vertex_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.agent.email}"
}

resource "google_cloud_run_v2_service" "agent" {
  count = var.deploy_agent ? 1 : 0

  name                = "${var.name_prefix}-agent"
  location            = var.region
  project             = var.project_id
  deletion_protection = false
  # No allUsers invoker binding: callers need an identity token and run.invoker.

  template {
    service_account = google_service_account.agent.email

    scaling {
      min_instance_count = 0 # scale to zero: no cost while idle
      max_instance_count = 2 # a hard ceiling on spend if someone floods it
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/agent:latest"

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "VERTEX_LOCATION"
        value = var.region
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }
  }

  # scripts/deploy_agent.sh rolls out new images; Terraform must not roll them back.
  lifecycle {
    ignore_changes = [template[0].containers[0].image, client, client_version, scaling]
  }

  depends_on = [
    google_project_service.fase4,
    google_bigquery_dataset_iam_member.agent_reads_gold,
    google_bigquery_connection_iam_member.agent_uses_connection,
  ]
}
