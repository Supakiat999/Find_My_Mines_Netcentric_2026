terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state is optional for this scaffold. Create the bucket/table first,
  # then uncomment and run `terraform init -migrate-state`.
  #
  # backend "s3" {
  #   bucket         = "find-my-mines-tfstate"
  #   key            = "dev/terraform.tfstate"
  #   region         = "ap-southeast-1"
  #   dynamodb_table = "find-my-mines-tfstate-lock"
  #   encrypt        = true
  # }
}
