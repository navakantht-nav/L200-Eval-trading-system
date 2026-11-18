locals {
  required_apis = [
    "aiplatform.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudtrace.googleapis.com",
    "logging.googleapis.com",
    "modelarmor.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "telemetry.googleapis.com",
  ]
}

resource "google_project_service" "enabled_apis" {
  for_each           = toset(local.required_apis)
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "containers" {
  location      = var.region
  repository_id = "alphapulse-containers"
  description   = "Container images for AlphaPulse ADKv2 & MCP Toolkit"
  format        = "DOCKER"

  depends_on = [google_project_service.enabled_apis]
}

resource "google_storage_bucket" "agent_runtime_staging" {
  name                        = "${var.project_id}-alphapulse-staging"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  depends_on = [google_project_service.enabled_apis]
}

resource "google_service_account" "mcp_gateway_sa" {
  account_id   = "alphapulse-mcp-gw"
  display_name = "AlphaPulse Cloud Run MCP & Streaming Gateway SA"
}

resource "google_project_iam_member" "least_privilege_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/cloudtrace.agent",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/telemetry.tracesWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.mcp_gateway_sa.email}"
}

resource "google_secret_manager_secret" "oauth_hmac_secret" {
  secret_id = "alphapulse-oauth-jwt-secret"

  replication {
    auto {}
  }

  depends_on = [google_project_service.enabled_apis]
}

resource "google_cloud_run_v2_service" "alphapulse_gateway" {
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.mcp_gateway_sa.email

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    containers {
      image = var.container_image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "TRUE"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }
      env {
        name  = "ALPHAPULSE_TRADING_MODE"
        value = "PAPER"
      }
      env {
        name  = "ALPHAPULSE_ENABLE_CLOUD_TRACE"
        value = "TRUE"
      }
      env {
        name  = "ALPHAPULSE_MAX_SINGLE_ORDER_USD"
        value = var.max_single_order_usd
      }
    }
  }

  depends_on = [
    google_project_iam_member.least_privilege_roles,
    google_artifact_registry_repository.containers,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "agent_identity_invoker" {
  count    = var.agent_runtime_effective_identity != "" ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.alphapulse_gateway.name
  role     = "roles/run.invoker"
  member   = var.agent_runtime_effective_identity
}
