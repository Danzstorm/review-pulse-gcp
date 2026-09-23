# Budget alert (spec section 7). Optional so `terraform apply` works for someone
# without billing-admin rights. Emails go to the billing account's admins.
data "google_project" "this" {
  project_id = var.project_id
}

resource "google_billing_budget" "monthly" {
  count           = var.billing_account == null ? 0 : 1
  provider        = google.billing
  billing_account = var.billing_account
  display_name    = "${var.name_prefix} monthly budget"

  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }

  # No currency_code: the API requires it to match the billing account's currency anyway.
  amount {
    specified_amount {
      units = tostring(var.budget_amount)
    }
  }

  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }

  depends_on = [google_project_service.fase1]
}
