terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# The Budgets API rejects user ADC calls without a quota project. Scoped to an
# alias so only the budget depends on user_project_override.
provider "google" {
  alias                 = "billing"
  project               = var.project_id
  user_project_override = true
  billing_project       = var.project_id
}
