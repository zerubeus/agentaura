"""Codex CLI adapter — implements AgentAdapter for OpenAI Codex."""

from __future__ import annotations

from pathlib import Path

from agentaura.adapters.base import AgentAdapter
from agentaura.adapters.codex.normalizer import normalize_codex_session
from agentaura.adapters.codex.parser import discover_codex_sessions, parse_codex_session
from agentaura.core.normalized import NormalizedSession


class CodexAdapter(AgentAdapter):
    @property
    def agent_name(self) -> str:
        return "Codex"

    def discover_sessions(self, data_dir: Path | None = None) -> list[Path]:
        return discover_codex_sessions(data_dir)

    def parse_and_normalize(self, session_path: Path) -> NormalizedSession:
        parsed = parse_codex_session(session_path)
        return normalize_codex_session(parsed)
