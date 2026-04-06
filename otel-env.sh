#!/usr/bin/env bash
# Source this before running Claude Code to enable OTel telemetry export.
# Usage: source otel-env.sh && claude
#
# Requires: AgentAura Docker Compose stack running (docker compose up -d)

export CLAUDE_CODE_ENABLE_TELEMETRY=1
export CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1

export OTEL_TRACES_EXPORTER=otlp
export OTEL_METRICS_EXPORTER=otlp
export OTEL_LOGS_EXPORTER=otlp

export OTEL_EXPORTER_OTLP_PROTOCOL=grpc
export OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317

# Include tool parameters and content in traces
export OTEL_LOG_TOOL_DETAILS=1
export OTEL_LOG_TOOL_CONTENT=1

echo "AgentAura OTel enabled → localhost:4317 (collector) → localhost:3000 (Langfuse)"
