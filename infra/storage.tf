# One bucket for raw/, dead-letter/ and seed/ prefixes (see spec section 2 diagram).
# Not three buckets: none of the three needs a different retention or access
# policy, so splitting them would just be three names for one behavior.
resource "google_storage_bucket" "review_pulse" {
  name                        = "${var.project_id}-data"
  location                    = var.region
  project                     = var.project_id
  force_destroy               = true # portfolio project: "terraform destroy" must leave it clean
  uniform_bucket_level_access = true

  depends_on = [google_project_service.fase1]
}
