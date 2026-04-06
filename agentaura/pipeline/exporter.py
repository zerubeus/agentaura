"""Langfuse batch exporter.

Orchestrates parsing → normalization → Langfuse export for sessions,
with import state tracking for idempotency.

State is only persisted after a successful flush to avoid marking sessions
as imported when the network send fails.
"""

from __future__ import annotations

import logging
from pathlib import Path

from langfuse import Langfuse

from agentaura.adapters.claude_code.mapper import export_session
from agentaura.adapters.claude_code.parser import (
    ParsedSession,
    discover_project_sessions,
    discover_sessions,
    parse_session,
)
from agentaura.core.normalized import NormalizedSession, normalize_session
from agentaura.pipeline.state import ImportState, session_checksum

logger = logging.getLogger(__name__)


def create_langfuse_client(
    public_key: str | None = None,
    secret_key: str | None = None,
    host: str | None = None,
) -> Langfuse:
    """Create a Langfuse client, falling back to env vars."""
    return Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        base_url=host,
    )


def import_all(
    langfuse: Langfuse,
    state: ImportState,
    claude_dir: Path | None = None,
    project_filter: str | None = None,
    limit: int | None = None,
    flush_every: int = 20,
) -> tuple[int, int, float]:
    """Import discovered sessions.

    Sessions are batched: export → flush → mark imported. State is only
    persisted after flush succeeds to avoid phantom imports.

    Returns (imported_count, skipped_count, total_cost).
    """
    if project_filter:
        base = (claude_dir or Path.home() / ".claude") / "projects"
        matching = [d for d in base.iterdir() if d.is_dir() and project_filter in d.name]
        session_paths: list[Path] = []
        for proj_dir in matching:
            session_paths.extend(discover_project_sessions(proj_dir))
        session_paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    else:
        session_paths = discover_sessions(claude_dir)

    if limit is not None:
        session_paths = session_paths[:limit]

    imported = 0
    skipped = 0
    total_cost = 0.0

    # Collect a batch of successfully exported sessions, then flush + mark
    pending_marks: list[tuple[str, Path, str, int, float]] = []

    for i, path in enumerate(session_paths):
        session_id = path.stem
        checksum = session_checksum(path)

        if state.is_imported(session_id, checksum):
            logger.debug("Skipping already-imported session %s", session_id)
            skipped += 1
            continue

        try:
            parsed: ParsedSession = parse_session(path)
            normalized: NormalizedSession = normalize_session(parsed)

            if not normalized.turns and not normalized.subagents:
                logger.debug("Skipping empty session %s", session_id)
                state.mark_imported(session_id, path, checksum, event_count=0, cost_usd=0.0)
                skipped += 1
                continue

            export_session(langfuse, normalized)

            event_count = normalized.total_generations + normalized.total_tool_calls
            pending_marks.append(
                (session_id, path, checksum, event_count, normalized.total_cost_usd)
            )
            imported += 1
            total_cost += normalized.total_cost_usd

            logger.info(
                "Imported %s (%d turns, $%.4f)",
                session_id[:12],
                len(normalized.turns),
                normalized.total_cost_usd,
            )
        except Exception:
            logger.exception("Failed to import %s", path)
            skipped += 1
            continue

        # Flush batch and persist state
        if (i + 1) % flush_every == 0 and pending_marks:
            langfuse.flush()
            for sid, sp, cs, ec, cost in pending_marks:
                state.mark_imported(sid, sp, cs, event_count=ec, cost_usd=cost)
            pending_marks.clear()

    # Final flush + mark
    if pending_marks:
        langfuse.flush()
        for sid, sp, cs, ec, cost in pending_marks:
            state.mark_imported(sid, sp, cs, event_count=ec, cost_usd=cost)

    return imported, skipped, total_cost
