# Cost guardrails.
#
# The budget is free while it has no budget actions, and alerts are evaluated
# once a day. AWS Budgets reads billing data from us-east-1 regardless of the
# project Region. Set budget_alert_emails in terraform.tfvars to receive the
# 80% actual and 100% forecasted alerts.

locals {
  budget_alerts = flatten([
    for email in var.budget_alert_emails : [
      { email = email, threshold = 80, type = "ACTUAL" },
      { email = email, threshold = 100, type = "FORECASTED" },
    ]
  ])
}

resource "aws_budgets_budget" "monthly" {
  provider = aws.us_east_1
  count    = var.monthly_budget_usd > 0 ? 1 : 0

  name         = "${local.name}-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = local.budget_alerts
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.threshold
      threshold_type             = "PERCENTAGE"
      notification_type          = notification.value.type
      subscriber_email_addresses = [notification.value.email]
    }
  }
}
