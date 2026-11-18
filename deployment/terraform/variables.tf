variable "project_id" {
  description = "Google Cloud Project ID hosting GEAP Agent Runtime, Cloud Run MCP Toolkit, and Model Armor"
  type        = string
}

variable "region" {
  description = "Primary GCP region for Cloud Run, Artifact Registry, and Agent Runtime"
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Cloud Run service name for the AlphaPulse Multi-Protocol Gateway & MCP Toolkit"
  type        = string
  default     = "alphapulse-trading-system"
}

variable "container_image" {
  description = "Container image URI in Artifact Registry"
  type        = string
}

variable "agent_runtime_effective_identity" {
  description = "Optional SPIFFE / IAM principal provisioned by GCP Agent Runtime (IdentityType.AGENT_IDENTITY)"
  type        = string
  default     = ""
}

variable "max_single_order_usd" {
  description = "Maximum single-order notional limit in USD enforced by risk engine"
  type        = string
  default     = "50000.0"
}
