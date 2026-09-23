resource "google_pubsub_topic" "reviews" {
  name    = "reviews"
  project = var.project_id

  depends_on = [google_project_service.fase1]
}

resource "google_pubsub_subscription" "reviews_dataflow" {
  name    = "reviews-dataflow-sub"
  project = var.project_id
  topic   = google_pubsub_topic.reviews.id

  ack_deadline_seconds = 60

  # Default TTL deletes a subscription after 31 idle days; this project sits idle between demos.
  expiration_policy {
    ttl = ""
  }
}
