variable "project_id" {
  description = "GCP project ID where Review Pulse resources are created."
  type        = string
}

variable "region" {
  description = "Default region for regional resources (bucket, BigQuery datasets)."
  type        = string
  default     = "us-central1"
}

variable "billing_account" {
  description = "Billing account ID (XXXXXX-XXXXXX-XXXXXX) for the budget alert. Leave null to skip the budget."
  type        = string
  default     = null
}

variable "budget_amount" {
  description = "Monthly budget, in the billing account's currency."
  type        = number
  default     = 20
}

variable "name_prefix" {
  description = "Prefix used to namespace resources (bucket, topic, service account)."
  type        = string
  default     = "review-pulse"
}
