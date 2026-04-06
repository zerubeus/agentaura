"""Abstract base class for coding agent adapters.

Each adapter knows how to discover, parse, and normalize sessions
from a specific coding agent's local data format.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from agentaura.core.normalized import NormalizedSession


class AgentAdapter(ABC):
    """Base class for coding agent session adapters."""

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """Human-readable agent name (e.g., 'Claude Code', 'Codex')."""

    @abstractmethod
    def discover_sessions(self, data_dir: Path | None = None) -> list[Path]:
        """Discover all session files for this agent.

        Returns paths sorted by modification time (newest first).
        """

    @abstractmethod
    def parse_and_normalize(self, session_path: Path) -> NormalizedSession:
        """Parse a session file and return a normalized session."""
