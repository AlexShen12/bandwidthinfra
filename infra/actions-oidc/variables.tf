variable "expected_account_id" {
  type        = string
  description = "Bandwidth-approved account; the provider refuses a different identity."
  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "Supply the approved 12-digit account ID."
  }
}
variable "region" {
  type    = string
  default = "us-west-2"
}
variable "bedrock_model_id" {
  type    = string
  default = "openai.gpt-oss-120b-1:0"
}
variable "github_oidc_subject" {
  type        = string
  description = "Exact actual custom OIDC sub including repo, ref, and workflow_ref for the approved default-branch infra-review workflow. Preserve immutable IDs when enabled."
  validation {
    condition     = can(regex("^repo:[^*?]+:ref:refs/heads/[^:*?]+:workflow_ref:[^*?]+/\\.github/workflows/infra-review\\.yml@refs/heads/[^:*?]+$", var.github_oidc_subject))
    error_message = "Use an exact custom subject containing the default branch and infra-review.yml workflow_ref; branch-only and wildcard subjects are not accepted."
  }
}
variable "create_oidc_provider" {
  type    = bool
  default = false
}
variable "existing_oidc_provider_arn" {
  type     = string
  nullable = true
  default  = null
  validation {
    condition     = var.existing_oidc_provider_arn == null ? true : can(regex("^arn:[^:]+:iam::[0-9]{12}:oidc-provider/token.actions.githubusercontent.com$", var.existing_oidc_provider_arn))
    error_message = "Reference an existing GitHub OIDC provider ARN."
  }
}
