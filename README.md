# AgentAura

Monitoring dashboard for coding agents. Imports session data from **Claude Code** and **Codex CLI** into a self-hosted [Langfuse](https://langfuse.com) instance for trace visualization, cost tracking, and token analysis.

## What you get

- **Trace visualization** — nested spans showing turns, LLM calls, tool executions, and subagents
- **Cost tracking** — per-session and aggregate cost computed from token usage
- **Token analytics** — input, output, cache creation, cache read breakdowns
- **Multi-agent support** — Claude Code and Codex CLI sessions in one dashboard
- **Live telemetry** — Claude Code's native OpenTelemetry export piped through an OTel Collector
- **Historical import** — batch import of all local session history

## Quick start

```bash
# Clone and set up
git clone https://github.com/zerubeus/agentaura.git
cd agentaura
uv venv && uv pip install -e ".[dev]"

# Start the monitoring stack (Langfuse + OTel Collector + Prometheus)
make up

# Wait ~30s for Langfuse to finish migrations, then import your sessions
agentaura import --agent all

# Open the dashboard
open http://localhost:3000
# Login: admin@agentaura.local / agentaura
# Go to Tracing in the left sidebar to see your sessions
```

## Commands

```bash
agentaura status                    # Show discovered sessions and import stats
agentaura import --agent all        # Import all Claude Code + Codex sessions
agentaura import --agent claude     # Import Claude Code sessions only
agentaura import --agent codex      # Import Codex CLI sessions only
agentaura import --limit 10         # Import first 10 sessions (for testing)
agentaura watch                     # Live watcher — auto-exports on session changes
```

## Live telemetry (Claude Code)

Source the env file before running Claude Code to get real-time traces in Langfuse:

```bash
source otel-env.sh
claude
```

This enables Claude Code's native OpenTelemetry export, which flows through the OTel Collector into Langfuse with per-request token counts, latency (TTFT), tool timing, and model info.

## Architecture

```
Claude Code ──[OTel]──► OTel Collector ──► Langfuse (traces)
                              │
                              └──► Prometheus (metrics)

~/.claude/projects/ ──► agentaura import ──► Langfuse
~/.codex/sessions/  ──► agentaura import ──► Langfuse
```

### Stack (all via `docker compose`)

| Service | Port | Purpose |
|---------|------|---------|
| Langfuse | :3000 | Dashboard UI and API |
| OTel Collector | :4317 | Receives OTLP from Claude Code |
| Prometheus | :9092 | Stores metrics from collector |
| ClickHouse | :8123 | Langfuse trace storage (OLAP) |
| PostgreSQL | :5433 | Langfuse metadata |
| Redis | :6379 | Langfuse cache |
| MinIO | :9090 | Langfuse blob storage |

## Data sources

### Claude Code (`~/.claude/projects/`)

Session JSONL files with full conversation history: user prompts, assistant responses (with model, token usage), tool calls (Read, Write, Bash, Glob, etc.), subagent trees (Explore, Plan, plugin agents), and tool-result sidecars.

### Codex CLI (`~/.codex/sessions/`)

Rollout JSONL files with session metadata, turn context, function calls, function call outputs, reasoning summaries, and cumulative token usage.

## Development

```bash
make check    # Run ruff + pyright + pytest
make test     # Run tests only
make lint     # Run ruff only
make typecheck # Run pyright only
```

## Makefile targets

```bash
make up       # Start Docker Compose stack
make down     # Stop stack
make logs     # Tail service logs
make status   # Show service status
make reset    # Stop and delete all data (destructive)
```
