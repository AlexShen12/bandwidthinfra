terraform {
  required_version = ">= 1.10, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.0, < 7.0"
    }
  }
  # Supply a dedicated, approved backend using -backend-config. Do not reuse
  # the older ingress stack's state or put credential values in Terraform.
  backend "s3" {}
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.expected_account_id]
  default_tags {
    tags = { Project = "InfrastructureAgent", Environment = "dev" }
  }
}

data "aws_partition" "current" {}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_oidc_provider ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

locals {
  provider_arn = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : var.existing_oidc_provider_arn
  model_arn    = "arn:${data.aws_partition.current.partition}:bedrock:${var.region}::foundation-model/${var.bedrock_model_id}"
}

resource "aws_iam_role" "reviewer" {
  name                 = "bandwidth-infra-reviewer-actions-dev"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = local.provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = var.github_oidc_subject
        }
      }
    }]
  })
  lifecycle {
    precondition {
      condition     = (var.create_oidc_provider && var.existing_oidc_provider_arn == null) || (!var.create_oidc_provider && var.existing_oidc_provider_arn != null)
      error_message = "Inventory IAM providers first. Either reference an existing GitHub provider or explicitly create one, never both."
    }
  }
}

resource "aws_iam_role_policy" "bedrock" {
  name = "invoke-approved-model"
  role = aws_iam_role.reviewer.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel"]
      Resource = [local.model_arn]
    }]
  })
}

output "bedrock_role_arn" {
  value = aws_iam_role.reviewer.arn
}
