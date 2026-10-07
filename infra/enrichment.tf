# Fase 3: BigQuery calls Gemini through a remote model. A BigQuery connection is the
# identity BigQuery uses to reach Vertex AI; its service account needs aiplatform.user.

resource "google_project_service" "fase3" {
  for_each = toset(["aiplatform.googleapis.com", "bigqueryconnection.googleapis.com"])

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_bigquery_connection" "vertex" {
  connection_id = "${var.name_prefix}-vertex"
  project       = var.project_id
  location      = var.region

  cloud_resource {}

  depends_on = [google_project_service.fase3]
}

resource "google_project_iam_member" "connection_uses_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_bigquery_connection.vertex.cloud_resource[0].service_account_id}"
}
