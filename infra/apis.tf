# Only the APIs Fase 1 needs. Vertex AI and Cloud Run are enabled in their
# own later phases (Fase 3 / Fase 4) — enabling them now would be unused scope.
locals {
  fase1_apis = [
    "pubsub.googleapis.com",
    "bigquery.googleapis.com",
    "storage.googleapis.com",
    "dataflow.googleapis.com",
    "billingbudgets.googleapis.com",
  ]
}

resource "google_project_service" "fase1" {
  for_each = toset(local.fase1_apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
