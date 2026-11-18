.PHONY: install lint format test eval run-web run-cli run-api run-mcp deploy-cloud-run deploy-agent-runtime

PROJECT_ID ?= $(GOOGLE_CLOUD_PROJECT)
REGION ?= us-central1
SERVICE_NAME ?= alphapulse-trading-system
MCP_SERVICE_NAME ?= alphapulse-mcp-toolkit

install:
	uv sync --all-groups

lint:
	uv run ruff check trading_agent mcp_toolkit app deployment tests
	uv run mypy trading_agent mcp_toolkit app

format:
	uv run ruff format trading_agent mcp_toolkit app deployment tests

test:
	uv run pytest -v --cov=trading_agent --cov=mcp_toolkit --cov=app

eval:
	GOOGLE_CLOUD_LOCATION=global uv run adk eval trading_agent evaluations/trading_desk.evalset.json --config_file_path=evaluations/test_config.json

run-web:
	uv run adk web .

run-cli:
	uv run adk run trading_agent

run-api:
	uv run uvicorn app.server:app --host 0.0.0.0 --port 8080 --reload

run-mcp:
	uv run python -m mcp_toolkit.server --transport streamable-http --port 8081

deploy-cloud-run:
	gcloud run deploy $(SERVICE_NAME) \
		--source . \
		--project $(PROJECT_ID) \
		--region $(REGION) \
		--no-allow-unauthenticated \
		--set-env-vars="GOOGLE_GENAI_USE_VERTEXAI=TRUE,GOOGLE_CLOUD_PROJECT=$(PROJECT_ID),GOOGLE_CLOUD_LOCATION=global,ALPHAPULSE_INFRA_REGION=$(REGION),ALPHAPULSE_ENABLE_CLOUD_TRACE=TRUE"

deploy-agent-runtime:
	uv run python deployment/deploy_agent_runtime.py \
		--project $(PROJECT_ID) \
		--location $(REGION)
