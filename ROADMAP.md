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

## Phase 2: Live Claude Code Trace Proof — DONE

Ran a Claude Code session with OTel enabled, verified traces appear in Langfuse.

### Deliverables

- [x] Verified OTel pipeline end-to-end: Python test trace → OTel Collector → Langfuse
- [x] Ran Claude Code with `source otel-env.sh && claude`
- [x] `LANGFUSE_INIT_ORG_ID` and `LANGFUSE_INIT_PROJECT_ID` required for headless init (fixed)
- [x] Traces visible in Langfuse with nested spans

### What Claude Code Native OTel Exports

**Trace types:**
| Span Name | Type | Attributes |
|---|---|---|
| `claude_code.llm_request` | Trace | model, input/output/cache tokens, duration_ms, ttft_ms, speed, attempt, success |
| `claude_code.tool` | Trace | tool_name, full_command, duration_ms |
| `claude_code.tool.execution` | Span (child) | duration_ms, success |
| `claude_code.tool.blocked_on_user` | Span (child) | decision, source, duration_ms |

**Common attributes on all spans:**
- `session.id` — maps to Langfuse sessionId
- `user.email`, `user.account_id`, `user.account_uuid`, `user.id`
- `organization.id`
- `terminal.type` (e.g., iTerm.app, vscode)
- `service.name` = claude-code, `service.version` = 2.1.92

**LLM request attributes:**
- `model` = claude-opus-4-6[1m]
- `input_tokens`, `output_tokens`, `cache_creation_tokens`, `cache_read_tokens`
- `duration_ms`, `ttft_ms` (time to first token)
- `speed` = normal
- `llm_request.context` = standalone

### Gap Analysis: Native OTel vs JSONL

| Data Point | Native OTel | JSONL | Notes |
|---|---|---|---|
| Model name | `model` attribute | `assistant.message.model` | Both have it |
| Input/output tokens | Individual attributes | `assistant.message.usage` | Both have it |
| Cache tokens | `cache_creation_tokens`, `cache_read_tokens` | `usage.cache_creation_input_tokens` | Both |
| Cost USD | Not exported (Langfuse computes from tokens) | Not in JSONL (we compute from pricing) | Neither has raw cost |
| Tool name | `tool_name` attribute | `tool_use.name` in content blocks | Both |
| Tool input params | `full_command` (Bash only) | Full `tool_use.input` dict | **JSONL is richer** |
| Tool output/result | Not in traces | `tool_result.content` in user events | **JSONL only** |
| User prompt text | Not in traces | `user.message.content` | **JSONL only** |
| Assistant response text | Not in traces | `assistant.message.content` | **JSONL only** |
| Thinking blocks | Not in traces | `thinking` content blocks (empty body) | JSONL has structure |
| Subagent trees | Not in traces | Subagent JSONLs + meta.json | **JSONL only** |
| TTFT (time to first token) | `ttft_ms` attribute | Not in JSONL | **OTel only** |
| Permission decisions | `tool.blocked_on_user` span | Not in JSONL | **OTel only** |
| Session duration | Span timestamps | `system.turn_duration` | Both |
| Git branch | Not in traces | `gitBranch` field | **JSONL only** |
| CWD / project | Not in traces | `cwd` field | **JSONL only** |
| MCP usage | Not in traces | `attachment` events | **JSONL only** |

### Conclusion

**Both data sources are needed:**
- **Native OTel** is best for live monitoring: real-time token counts, latency (TTFT), tool timing, permission decisions. Automatically flows into Langfuse.
- **JSONL batch import** is needed for: prompt/response content, tool input/output details, subagent trees, project/branch context, historical data. This is Phase 3.
- The watcher (Phase 4) remains valuable as a fallback when OTel env vars aren't set.

---

## Phase 3: Historical Batch Importer — DONE

Import existing sessions into Langfuse using the Python SDK v4.

### Deliverables

- [x] `agentaura/adapters/claude_code/mapper.py` — Maps normalized sessions to Langfuse objects
  - Session → root span (via `propagate_attributes` for session_id, tags, metadata)
  - Turn → child span (user prompt as input, permission_mode, duration)
  - LLM API call → generation (model, usage_details, cost_details)
  - Tool use → tool span (name, input params, output content)
  - Subagent → agent span (agent_type, description, nested generations)
- [x] `agentaura/pipeline/exporter.py` — Orchestrates parse → normalize → export with batched flushing
- [x] `agentaura/pipeline/state.py` — SQLite-backed idempotent import state (file checksum tracking)
- [x] CLI commands:
  - `agentaura import` — Import all sessions (with --limit, --project, --host, --verbose)
  - `agentaura status` — Show discovered/imported/remaining counts and total cost
- [x] Verified against live Langfuse: 3 sessions imported, nested spans visible, costs computed
- [x] Idempotent re-run confirmed: unchanged sessions skipped, changed sessions re-imported

### Verified Import Results

| Session | Observations | Cost |
|---|---|---|
| dazzling-swimming-neumann | 1,417 | $174.28 |
| wiggly-tinkering-wolf | 37 | $2.15 |
| calm-tinkering-frost | 6 | $0.98 |

---

## Phase 4: File Watcher Fallback — DONE

Real-time import of sessions when OTel env vars aren't set.

### Deliverables

- [x] `agentaura/watcher/file_watcher.py` — watchdog-based watcher on `~/.claude/projects/`
  - Debounced (5s) to handle rapid JSONL appends during active sessions
  - Filters to top-level session JSONLs only (skips subagent files)
  - Re-exports full session on each change
- [x] `agentaura watch` CLI command — foreground daemon with Ctrl+C to stop
  - Connects to Langfuse, watches for changes, auto-exports
- [x] Verified: watcher fires callback after file modification settles

---

## Phase 5: Multi-Agent Adapters — DONE

Support Codex, OpenClaw, and other coding agents.

### Deliverables

- [x] `agentaura/adapters/base.py` — Abstract `AgentAdapter` base class (discover, parse, normalize)
- [x] `agentaura/adapters/claude_code/adapter.py` — Claude Code adapter
- [x] `agentaura/adapters/codex/` — Full Codex CLI adapter:
  - Parser reads `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`
  - Handles event types: session_meta, turn_context, response_item (message, function_call, function_call_output, reasoning), event_msg (user_message, token_count)
  - Normalizes to same Session/Turn/Generation/ToolCall hierarchy
  - Verified: 123 real Codex sessions parsed with turns, generations, tool calls, token counts
- [ ] `adapters/openclaw/` — Deferred (not installed on this machine, documented format at `~/.openclaw/agents/<id>/sessions/`)
- [ ] Unified cross-agent dashboard views
- [ ] Agent comparison metrics

### Codex Session Format (Discovered)

| Event Type | Payload | Maps To |
|---|---|---|
| `session_meta` | id, cwd, cli_version, git, model_provider, source | Session metadata |
| `turn_context` | model, effort, sandbox_policy, cwd | Turn context |
| `event_msg/user_message` | message text | Turn start (user prompt) |
| `event_msg/token_count` | total_token_usage, last_token_usage | Token tracking |
| `response_item/message` | role, content[] | Generation (assistant response) |
| `response_item/function_call` | name, arguments, call_id | Tool call |
| `response_item/function_call_output` | call_id, output | Tool result |
| `response_item/reasoning` | summary, encrypted_content | Thinking flag |

### Codex Data Location
- Sessions: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl`
- Index: `~/.codex/session_index.jsonl`
- SQLite: `~/.codex/state_5.sqlite` (114 threads with model, tokens, git context)
- Config: `~/.codex/config.toml` (includes `[otel]` section)
- Has native OTel support (traces, logs, metrics)

---

## Reference Projects

- [Claude Code monitoring docs](https://code.claude.com/docs/en/monitoring-usage) — Native OTel env vars
- [Langfuse self-hosting](https://langfuse.com/self-hosting) — Infra requirements
- [TechNickAI/claude_telemetry](https://github.com/TechNickAI/claude_telemetry) — OTel wrapper reference
- [ColeMurray/claude-code-otel](https://github.com/ColeMurray/claude-code-otel) — Another reference
- [OTel GenAI semantic conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) — Reference only (status: Development)
- [langfuse PyPI v4.0.0](https://pypi.org/project/langfuse/) — Current SDK version
