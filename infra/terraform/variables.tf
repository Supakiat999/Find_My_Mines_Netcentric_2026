variable "aws_region" {
  description = "Project Region. It must match the Region assigned in AWS Settings."
  type        = string
  default     = "ap-southeast-1"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must look like ap-southeast-1."
  }
}

variable "aws_profile" {
  description = "AWS CLI profile Terraform uses. Set to null to use the default credential chain."
  type        = string
  default     = "agent-toolkit"
  nullable    = true
}

variable "project" {
  description = "Name prefix for every resource."
  type        = string
  default     = "find-my-mines"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,24}$", var.project))
    error_message = "project must be lowercase letters, digits, and hyphens."
  }
}

variable "environment" {
  description = "Environment suffix, for example dev."
  type        = string
  default     = "dev"
}

variable "image_tag" {
  description = "ECR image tag the host should run."
  type        = string
  default     = "latest"
}

variable "instance_type" {
  description = "EC2 instance type. t3.small is enough for one game server."
  type        = string
  default     = "t3.small"
}

variable "ssh_public_key_path" {
  description = "Public key registered for the host. The private key stays local."
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "ssh_cidrs" {
  description = "CIDRs allowed to reach TCP 22. Use your public IP /32."
  type        = list(string)

  validation {
    condition     = length(var.ssh_cidrs) > 0
    error_message = "Set ssh_cidrs to at least one CIDR, for example [\"203.0.113.10/32\"]."
  }
}

variable "allowed_cidrs" {
  description = "CIDRs allowed to reach the game port. Restrict this for a private demo."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "vpc_cidr" {
  description = "CIDR for the dedicated VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_engine_version" {
  description = "PostgreSQL major version. RDS picks the current minor release."
  type        = string
  default     = "17"
}

variable "db_allocated_storage" {
  description = "Initial RDS storage in GiB. Autoscaling doubles it at most."
  type        = number
  default     = 20
}

variable "db_backup_retention_days" {
  description = "Automated backup retention. Set 0 to disable for a throwaway demo."
  type        = number
  default     = 7
}

variable "db_skip_final_snapshot" {
  description = "Skip the final snapshot on destroy. true is convenient for a demo."
  type        = bool
  default     = true
}

variable "db_deletion_protection" {
  description = "Block deletion of the database."
  type        = bool
  default     = false
}

variable "log_retention_days" {
  description = "CloudWatch Logs retention for the game container."
  type        = number
  default     = 14
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget in USD. Set 0 to skip creating the budget."
  type        = number
  default     = 50

  validation {
    condition     = var.monthly_budget_usd >= 0
    error_message = "monthly_budget_usd cannot be negative."
  }
}

variable "budget_alert_emails" {
  description = "Emails that receive 80% actual and 100% forecasted budget alerts."
  type        = list(string)
  default     = []
}
