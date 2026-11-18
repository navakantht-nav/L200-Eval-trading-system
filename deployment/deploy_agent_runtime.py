"""Deploys AlphaPulse to GCP Agent Runtime with SPIFFE Agent Identity enabled.

Supports the Gemini Enterprise Agent Platform (GEAP) `agentplatform` v2 SDK
(`IdentityType.AGENT_IDENTITY` with certificate-bound mTLS Context-Aware Access)
and gracefully falls back to `vertexai.agent_engines` when targeting v1 endpoints.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def deploy_with_agent_identity(
    project: str,
    location: str,
    staging_bucket: str | None = None,
    display_name: str = "alphapulse-trading-desk",
) -> None:
    from trading_agent.agent import app as adk_app

    requirements = [
        "google-adk[a2a,eval,otel]>=2.0.0",
        "google-cloud-aiplatform[adk,agent_engines]>=2.2.0",
        "mcp>=1.6.0",
        "pydantic>=2.9.0",
        "cryptography>=43.0.0",
        "opentelemetry-exporter-gcp-trace>=1.8.0",
    ]

    try:
        import agentplatform
        from agentplatform import types
        from agentplatform.frameworks import AdkApp

        client = agentplatform.Client(
            project=project,
            location=location,
            http_options={"api_version": "v1beta1"},
        )
        config: dict[str, object] = {
            "display_name": display_name,
            "identity_type": types.IdentityType.AGENT_IDENTITY,
            "requirements": requirements,
        }
        if staging_bucket:
            config["staging_bucket"] = (
                staging_bucket
                if staging_bucket.startswith("gs://")
                else f"gs://{staging_bucket}"
            )

        remote_runtime = client.runtimes.create(
            agent=AdkApp(app=adk_app),
            config=config,
        )
        effective_id = getattr(
            getattr(remote_runtime.api_resource, "spec", None),
            "effective_identity",
            "provisioned",
        )
        print(f"Deployed to GCP Agent Runtime: {remote_runtime.api_resource.name}")
        print(f"SPIFFE / IAM Effective Agent Identity: {effective_id}")
        return
    except ImportError:
        import vertexai
        from vertexai import agent_engines

        vertexai.init(project=project, location=location, staging_bucket=staging_bucket)
        remote_engine = agent_engines.create(
            agent_engine=adk_app,
            display_name=display_name,
            requirements=requirements,
            extra_packages=["trading_agent", "mcp_toolkit"],
        )
        print(f"Deployed to Vertex AI Agent Engine: {remote_engine.resource_name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy AlphaPulse to GCP Agent Runtime")
    parser.add_argument("--project", default=os.getenv("GOOGLE_CLOUD_PROJECT", ""))
    parser.add_argument(
        "--location", default=os.getenv("ALPHAPULSE_INFRA_REGION", "us-central1")
    )
    parser.add_argument("--staging-bucket", default=os.getenv("ALPHAPULSE_STAGING_BUCKET"))
    parser.add_argument("--display-name", default="alphapulse-trading-desk")
    args = parser.parse_args()

    if not args.project:
        raise SystemExit("Error: --project or GOOGLE_CLOUD_PROJECT is required.")

    deploy_with_agent_identity(
        project=args.project,
        location=args.location,
        staging_bucket=args.staging_bucket,
        display_name=args.display_name,
    )


if __name__ == "__main__":
    main()
