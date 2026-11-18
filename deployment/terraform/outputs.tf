output "cloud_run_gateway_uri" {
  description = "HTTPS endpoint for AlphaPulse Multi-Protocol Gateway, MCP (/mcp), & A2A"
  value       = google_cloud_run_v2_service.alphapulse_gateway.uri
}

output "mcp_streamable_http_endpoint" {
  description = "Streamable HTTP MCP endpoint URI"
  value       = "${google_cloud_run_v2_service.alphapulse_gateway.uri}/mcp"
}

output "agent_runtime_staging_bucket" {
  description = "GCS bucket used for GCP Agent Runtime deployment artifacts"
  value       = google_storage_bucket.agent_runtime_staging.url
}

output "gateway_service_account_email" {
  description = "Least-privilege Cloud Run service account email"
  value       = google_service_account.mcp_gateway_sa.email
}
