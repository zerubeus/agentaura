"""Parser for Claude Code local session data.

Reads JSONL session files, subagent JSONLs + meta.json, and tool-results sidecars.
Builds a tree structure from flat events using uuid/parentUuid relationships.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from agentaura.core.events import (
    SessionEvent,
    SubagentMeta,
    parse_event,
)

logger = logging.getLogger(__name__)


@dataclass
class SubagentData:
    """A parsed subagent with its metadata and events."""

    agent_id: str
    meta: SubagentMeta | None
    events: list[SessionEvent]


@dataclass
class ParsedSession:
    """Complete parsed session with all related data."""

    session_id: str
    project_path: str
    jsonl_path: Path
    events: list[SessionEvent]
    subagents: list[SubagentData] = field(default_factory=list)
    tool_results: dict[str, str] = field(default_factory=dict)  # hash -> content

    @property
    def event_count(self) -> int:
        return len(self.events) + sum(len(s.events) for s in self.subagents)


def _parse_jsonl(path: Path) -> list[SessionEvent]:
    """Parse a JSONL file into a list of SessionEvents."""
    events: list[SessionEvent] = []
    with open(path) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                event = parse_event(raw)
                if event is not None:
                    events.append(event)
            except (json.JSONDecodeError, Exception) as e:
                logger.debug("Skipping line %d in %s: %s", line_num, path, e)
    return events


def _parse_subagents(session_dir: Path) -> list[SubagentData]:
    """Parse all subagent data from a session directory."""
    subagents_dir = session_dir / "subagents"
    if not subagents_dir.is_dir():
        return []

    subagents: list[SubagentData] = []
    seen_ids: set[str] = set()

    for meta_path in sorted(subagents_dir.glob("*.meta.json")):
        # Extract agent ID: agent-{id}.meta.json -> {id}
        agent_id = meta_path.stem.replace(".meta", "")

        if agent_id in seen_ids:
            continue
        seen_ids.add(agent_id)

        # Parse meta
        meta = None
        try:
            raw = json.loads(meta_path.read_text())
            meta = SubagentMeta.model_validate(raw)
        except Exception as e:
            logger.debug("Failed to parse subagent meta %s: %s", meta_path, e)

        # Parse corresponding JSONL
        jsonl_path = subagents_dir / f"{agent_id}.jsonl"
        events: list[SessionEvent] = []
        if jsonl_path.exists():
            events = _parse_jsonl(jsonl_path)

        subagents.append(SubagentData(agent_id=agent_id, meta=meta, events=events))

    # Also pick up JSONL files without corresponding meta
    for jsonl_path in sorted(subagents_dir.glob("*.jsonl")):
        agent_id = jsonl_path.stem
        if agent_id in seen_ids:
            continue
        seen_ids.add(agent_id)
        events = _parse_jsonl(jsonl_path)
        subagents.append(SubagentData(agent_id=agent_id, meta=None, events=events))

    return subagents


def _parse_tool_results(session_dir: Path) -> dict[str, str]:
    """Parse tool-results sidecar files from a session directory."""
    tool_results_dir = session_dir / "tool-results"
    if not tool_results_dir.is_dir():
        return {}

    results: dict[str, str] = {}
    for result_path in tool_results_dir.iterdir():
        if result_path.is_file():
            try:
                results[result_path.stem] = result_path.read_text()
            except Exception as e:
                logger.debug("Failed to read tool result %s: %s", result_path, e)
    return results


def parse_session(jsonl_path: Path, project_path: str | None = None) -> ParsedSession:
    """Parse a single session from its JSONL file.

    Also loads subagents and tool-results from the corresponding session directory.
    """
    session_id = jsonl_path.stem
    if project_path is None:
        project_path = jsonl_path.parent.name

    events = _parse_jsonl(jsonl_path)

    # Session directory (same name as JSONL, without extension)
    session_dir = jsonl_path.parent / session_id
    subagents = _parse_subagents(session_dir)
    tool_results = _parse_tool_results(session_dir)

    return ParsedSession(
        session_id=session_id,
        project_path=project_path,
        jsonl_path=jsonl_path,
        events=events,
        subagents=subagents,
        tool_results=tool_results,
    )


def discover_sessions(claude_dir: Path | None = None) -> list[Path]:
    """Discover all session JSONL files under the Claude projects directory.

    Returns paths sorted by modification time (newest first).
    """
    if claude_dir is None:
        claude_dir = Path.home() / ".claude"

    projects_dir = claude_dir / "projects"
    if not projects_dir.is_dir():
        return []

    sessions = list(projects_dir.glob("*/*.jsonl"))
    # Exclude subagent JSONLs (they live inside session subdirectories)
    sessions = [s for s in sessions if "subagents" not in s.parts]
    sessions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return sessions


def discover_project_sessions(project_dir: Path) -> list[Path]:
    """Discover session JSONL files for a specific project directory."""
    sessions = list(project_dir.glob("*.jsonl"))
    sessions.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return sessions


def parse_all_sessions(
    claude_dir: Path | None = None,
    limit: int | None = None,
) -> list[ParsedSession]:
    """Parse all sessions from the Claude projects directory.

    Args:
        claude_dir: Path to ~/.claude (defaults to ~/.claude)
        limit: Maximum number of sessions to parse (newest first)
    """
    session_paths = discover_sessions(claude_dir)
    if limit is not None:
        session_paths = session_paths[:limit]

    parsed: list[ParsedSession] = []
    for path in session_paths:
        try:
            session = parse_session(path)
            parsed.append(session)
        except Exception as e:
            logger.warning("Failed to parse session %s: %s", path, e)

    return parsed
