"""Parser for OpenAI Codex CLI local session data.

Reads JSONL rollout files from ~/.codex/sessions/YYYY/MM/DD/.
Extracts session metadata, turns, tool calls, and token usage.

Codex JSONL event types:
    session_meta     — Session metadata (id, cwd, model_provider, git info)
    turn_context     — Turn-level context (model, effort, sandbox_policy)
    response_item    — API response items:
        message      — User/assistant messages
        function_call — Tool invocations
        function_call_output — Tool results
        reasoning    — Model reasoning (summary only, content encrypted)
    event_msg        — Events:
        user_message — User prompt text
        token_count  — Cumulative and per-turn token usage
        task_started, task_completed, etc.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CodexEvent:
    """A single event from a Codex session JSONL file."""

    timestamp: datetime | None
    type: str
    payload: dict[str, Any]


@dataclass
class ParsedCodexSession:
    """Parsed Codex session with all events."""

    session_id: str
    jsonl_path: Path
    events: list[CodexEvent]
    meta: dict[str, Any] = field(default_factory=dict)


def _parse_timestamp(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def parse_codex_session(jsonl_path: Path) -> ParsedCodexSession:
    """Parse a Codex rollout JSONL file."""
    events: list[CodexEvent] = []
    meta: dict[str, Any] = {}
    session_id = ""

    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = raw.get("type", "unknown")
            payload = raw.get("payload", {})
            ts = _parse_timestamp(raw.get("timestamp"))

            if event_type == "session_meta":
                meta = payload
                session_id = payload.get("id", jsonl_path.stem)

            events.append(CodexEvent(timestamp=ts, type=event_type, payload=payload))

    if not session_id:
        session_id = jsonl_path.stem

    return ParsedCodexSession(
        session_id=session_id,
        jsonl_path=jsonl_path,
        events=events,
        meta=meta,
    )


def discover_codex_sessions(codex_dir: Path | None = None) -> list[Path]:
    """Discover all Codex session JSONL files.

    Returns paths sorted by modification time (newest first).
    """
    if codex_dir is None:
        codex_dir = Path.home() / ".codex"

    sessions_dir = codex_dir / "sessions"
    if not sessions_dir.is_dir():
        return []

    sessions = list(sessions_dir.rglob("rollout-*.jsonl"))
    sessions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return sessions
