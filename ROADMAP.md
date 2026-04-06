# AgentAura Roadmap

Monitoring dashboard for coding agents (Claude Code, Codex, OpenClaw), similar to Logfire.

## Architecture

```
                LIVE SESSIONS                              HISTORICAL DATA
                ─────────────                              ───────────────
  Claude Code                                    ~/.claude/projects/*/*.jsonl
      │                                          ~/.claude/projects/*/subagents/
      │ CLAUDE_CODE_ENABLE_TELEMETRY=1           ~/.claude/projects/*/tool-results/
      │ CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1
      │ OTEL_*_EXPORTER=otlp
      │ OTEL_EXPORTER_OTLP_ENDPOINT=:4317
      │                                                     │
      ▼                                                     ▼
  ┌────────────────────┐                       agentaura CLI (Python)
  │  OTel Collector    │                       ├── JSONL parser
  │  (mandatory)       │                       ├── Subagent linker
  │  localhost:4317    │                       ├── Cost calculator
  │                    │                       └── Langfuse SDK exporter
  │  Routes:           │                                    │
  │  traces → Langfuse │                                    │
  │  metrics → Prom    │                                    │
  │  logs → debug      │                                    │
  └────────┬───────────┘                                    │
           │                                                │
           ▼                                                ▼
  ┌─────────────────────────────────────────────────────────────┐
  │                    Docker Compose Stack                      │
  │                                                             │
  │  ┌───────────┐ ┌──────────┐ ┌─────────┐ ┌──────┐ ┌──────┐│
  │  │ Langfuse  │ │ClickHouse│ │Postgres │ │Redis │ │MinIO ││
  │  │ :3000     │ │ :8123    │ │ :5433   │ │:6379 │ │:9090 ││
  │  └───────────┘ └──────────┘ └─────────┘ └──────┘ └──────┘│
  │  ┌──────────────┐  ┌────────────┐                          │
  │  │ OTel Collect. │  │ Prometheus │                          │
  │  │ :4317/:4318   │  │ :9092      │                          │
  │  └──────────────┘  └────────────┘                          │
  └─────────────────────────────────────────────────────────────┘
```

## Tech Stack

- **Trace backend**: Langfuse v3 (self-hosted, MIT) — Postgres, ClickHouse, Redis, MinIO
- **Signal routing**: OTel Collector — traces to Langfuse, metrics to Prometheus
- **CLI/Importer**: Python 3.13 — langfuse v4, pydantic v2, typer, watchdog
- **Data model**: Internal normalized model (OTel GenAI conventions as reference, not direct — still "Development" status)

---

## Phase 0: Schema Spike — DONE

Parse real Claude Code sessions, document every edge case, build robust Pydantic models from actual data.

### Deliverables

- [x] `agentaura/core/events.py` — Raw Pydantic models for all 11 JSONL event types
  - 100% parse rate across 36,012 real events
  - Handles: user, assistant, system, progress, queue-operation, attachment, file-history-snapshot, permission-mode, agent-name, custom-title, last-prompt
  - Preserves all source IDs: uuid, parentUuid, requestId, toolUseID, parentToolUseID, promptId, agentId
- [x] `agentaura/adapters/claude_code/parser.py` — JSONL + subagent + tool-results sidecar reader
  - Discovers sessions across all projects
  - Parses subagent `.meta.json` and `.jsonl` files
  - Reads `tool-results/` sidecar files for large tool outputs
- [x] `agentaura/core/normalized.py` — Normalized Session/Turn/Generation/ToolCall hierarchy
  - Reconstructs conversation tree from uuid/parentUuid
  - Handles parallel tool calls (multiple tool_use blocks per assistant response)
  - Handles multimodal user prompts (text + image/document blocks)
  - Surfaces tool-result sidecars when inline content is empty
  - Links subagent generations and tool calls
- [x] `agentaura/core/pricing.py` — Model pricing table and cost computation
  - Opus, Sonnet, Haiku with cache write/read pricing
  - Fallback pricing for unknown models
- [x] `agentaura/cli.py` — Stub CLI entry point
- [x] Tests — 23 tests passing (parser, normalizer, pricing)
- [x] Project setup — pyproject.toml (uv, pyright, ruff), .pre-commit-config.yaml, uv.lock, .gitignore

### Schema Caveats Documented

- Thinking blocks have empty body (not visible), signature only
- `effortLevel` is a global setting, not per-request
- `session_costs.txt` has sparse data — use for coarse validation only
- `<synthetic>` model used for API error messages

---

## Phase 1: Infrastructure — DONE

Docker Compose stack with OTel Collector routing signals properly.

### Deliverables

- [x] `docker-compose.yml` — 8 services + minio-init
  - Langfuse v3 web (`:3000`) + worker (`:3030`)
  - PostgreSQL 17 (`:5433`)
  - ClickHouse (`:8123`)
  - Redis 7 (`:6379`)
  - MinIO (`:9090`)
  - OTel Collector (`:4317` gRPC, `:4318` HTTP)
  - Prometheus (`:9092`)
- [x] `otel-collector-config.yaml` — OTLP receiver, traces to Langfuse, metrics to Prometheus
- [x] `prometheus.yml` — scrapes collector metrics
- [x] `.env.example` — all config keys with auto-init (org, project, user, API keys)
- [x] `Makefile` — up, down, logs, status, reset, check targets
- [x] `otel-env.sh` — source before Claude Code to enable OTel export

### Issues Fixed During Setup

- `CLICKHOUSE_CLUSTER_ENABLED=false` required to avoid Zookeeper errors in single-node ClickHouse
- Port 5432 conflict — Postgres exposed on 5433 externally
- Prometheus port conflict — moved from 9091 to 9092

### Verification

- All 8 services running and healthy
- Langfuse UI at `localhost:3000` returns HTTP 200
- OTel Collector accepting OTLP at `localhost:4317` (gRPC) and `localhost:4318` (HTTP)
- Prometheus at `localhost:9092` returns HTTP 200

---

## Phase 2: Live Claude Code Trace Proof — TODO

Run a Claude Code session with OTel enabled and verify traces appear in Langfuse.

### Plan

- [ ] Source `otel-env.sh` and run a real Claude Code session
- [ ] Verify in Langfuse: trace with nested spans, tool calls, token counts, model info, cost
- [ ] Document gaps: what Claude's native OTel exports vs what's missing
- [ ] Decide if native OTel is sufficient for live, or if we also need the watcher

---

## Phase 3: Historical Batch Importer — TODO

Import all ~1,800 existing sessions into Langfuse using the Python SDK.

### Plan

- [ ] `agentaura/adapters/claude_code/mapper.py` — Normalized events to Langfuse objects
  - Session → `langfuse.trace()`
  - Turn → `trace.span()`
  - LLM API call → `span.generation()`
  - Tool use → `span.span()`
  - Subagent → child trace/span linked via parentToolUseID
- [ ] `agentaura/pipeline/exporter.py` — Langfuse SDK v4 exporter
- [ ] `agentaura/pipeline/state.py` — SQLite import state (idempotent re-runs)
- [ ] CLI commands: `agentaura import`, `agentaura import --project <path>`, `agentaura status`
- [ ] Validate costs against `session_costs.txt` where available

---

## Phase 4: File Watcher Fallback — TODO

Real-time import of sessions when OTel env vars aren't set.

### Plan

- [ ] `agentaura/watcher/file_watcher.py` — watchdog on `~/.claude/projects/`
- [ ] `agentaura watch` CLI command (daemon mode)
- [ ] Incremental parsing (track file byte offset, process only appended lines)
- [ ] Handle subagent JSONL creation mid-session

---

## Phase 5: Multi-Agent Adapters — TODO

Support Codex, OpenClaw, and other coding agents.

### Plan

- [ ] Abstract `AgentAdapter` base in `agentaura/adapters/base.py`
- [ ] `adapters/codex/` — Research Codex session format, implement adapter
- [ ] `adapters/openclaw/` — Research OpenClaw log format, implement adapter
- [ ] Unified cross-agent dashboard views
- [ ] Agent comparison metrics

---

## Reference Projects

- [Claude Code monitoring docs](https://code.claude.com/docs/en/monitoring-usage) — Native OTel env vars
- [Langfuse self-hosting](https://langfuse.com/self-hosting) — Infra requirements
- [TechNickAI/claude_telemetry](https://github.com/TechNickAI/claude_telemetry) — OTel wrapper reference
- [ColeMurray/claude-code-otel](https://github.com/ColeMurray/claude-code-otel) — Another reference
- [OTel GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) — Reference only (status: Development)
- [langfuse PyPI v4.0.0](https://pypi.org/project/langfuse/) — Current SDK version
