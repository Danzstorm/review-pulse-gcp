# Experiment: ELT alternative to the Dataflow pipeline, running side by side.
# A second subscription on the same topic receives its own copy of every message;
# Pub/Sub writes it raw into BigQuery (no workers, no code) and the validation runs
# in SQL (view below). Delete this file and apply to drop the experiment.

resource "google_bigquery_table" "bronze_reviews_raw_bqsub" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.bronze.dataset_id
  table_id            = "reviews_raw_bqsub"
  deletion_protection = false

  # Layout required by a BigQuery subscription with write_metadata and no schema:
  # `data` holds the raw payload; STRING (not JSON) so malformed payloads land too.
  schema = jsonencode([
    { name = "subscription_name", type = "STRING", mode = "NULLABLE" },
    { name = "message_id", type = "STRING", mode = "NULLABLE" },
    { name = "publish_time", type = "TIMESTAMP", mode = "NULLABLE" },
    { name = "data", type = "STRING", mode = "NULLABLE" },
    { name = "attributes", type = "STRING", mode = "NULLABLE" },
  ])

  time_partitioning {
    type  = "DAY"
    field = "publish_time"
  }
}

# Table-scoped, not dataset-scoped: the Pub/Sub service agent can write this table only.
resource "google_bigquery_table_iam_member" "pubsub_writes_bqsub_table" {
  project    = var.project_id
  dataset_id = google_bigquery_table.bronze_reviews_raw_bqsub.dataset_id
  table_id   = google_bigquery_table.bronze_reviews_raw_bqsub.table_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_subscription" "reviews_bigquery" {
  name    = "reviews-bigquery-sub"
  project = var.project_id
  topic   = google_pubsub_topic.reviews.id

  bigquery_config {
    table          = "${var.project_id}.${google_bigquery_table.bronze_reviews_raw_bqsub.dataset_id}.${google_bigquery_table.bronze_reviews_raw_bqsub.table_id}"
    write_metadata = true
  }

  expiration_policy {
    ttl = ""
  }

  # Pub/Sub checks write access when the subscription is created.
  depends_on = [google_bigquery_table_iam_member.pubsub_writes_bqsub_table]
}

resource "google_bigquery_table" "bronze_reviews_bqsub_classified" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.bronze.dataset_id
  table_id            = "reviews_bqsub_classified"
  deletion_protection = false

  view {
    query          = templatefile("${path.module}/../sql/experiments/bqsub_classified.sql", { project = var.project_id })
    use_legacy_sql = false
  }

  depends_on = [google_bigquery_table.bronze_reviews_raw_bqsub]
}
