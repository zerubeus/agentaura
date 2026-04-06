"""Claude Code adapter — implements AgentAdapter for Claude Code CLI."""

from __future__ import annotations

from pathlib import Path

from agentaura.adapters.base import AgentAdapter
from agentaura.adapters.claude_code.parser import discover_sessions, parse_session
from agentaura.core.normalized import NormalizedSession, normalize_session


class ClaudeCodeAdapter(AgentAdapter):
    @property
    def agent_name(self) -> str:
        return "Claude Code"

    def discover_sessions(self, data_dir: Path | None = None) -> list[Path]:
        return discover_sessions(data_dir)

    def parse_and_normalize(self, session_path: Path) -> NormalizedSession:
        parsed = parse_session(session_path)
        return normalize_session(parsed)
