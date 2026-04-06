"""Import state tracking via SQLite.

Tracks which sessions have been imported to Langfuse, with content checksums
for idempotent re-runs. The checksum covers the main JSONL plus any subagent
and tool-result sidecar files.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class ImportState:
    """SQLite-backed import state tracker."""

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = Path.home() / ".claude" / "agentaura_import.db"
        self._db_path = db_path
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS imported_sessions (
                session_id TEXT PRIMARY KEY,
                jsonl_path TEXT NOT NULL,
                file_checksum TEXT NOT NULL,
                imported_at TEXT NOT NULL,
                event_count INTEGER,
                cost_usd REAL
            )
        """)
        self._conn.commit()

    def is_imported(self, session_id: str, checksum: str) -> bool:
        """Check if a session has already been imported with the same content."""
        row = self._conn.execute(
            "SELECT file_checksum FROM imported_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return False
        return row[0] == checksum

    def mark_imported(
        self,
        session_id: str,
        jsonl_path: Path,
        checksum: str,
        event_count: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Mark a session as imported."""
        self._conn.execute(
            """INSERT OR REPLACE INTO imported_sessions
               (session_id, jsonl_path, file_checksum, imported_at, event_count, cost_usd)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                str(jsonl_path),
                checksum,
                datetime.now(UTC).isoformat(),
                event_count,
                cost_usd,
            ),
        )
        self._conn.commit()

    def get_stats(self) -> dict[str, int | float]:
        """Get import statistics."""
        row = self._conn.execute(
            """SELECT COUNT(*), COALESCE(SUM(event_count), 0), COALESCE(SUM(cost_usd), 0)
               FROM imported_sessions"""
        ).fetchone()
        assert row is not None
        return {
            "imported_sessions": row[0],
            "total_events": row[1],
            "total_cost_usd": row[2],
        }

    def close(self) -> None:
        self._conn.close()


def session_checksum(jsonl_path: Path) -> str:
    """Compute a checksum covering the main JSONL and all related files.

    Includes subagent JSONLs/meta files and tool-result sidecars so that
    changes to any part of the session invalidate the import.
    """
    h = hashlib.md5()
    session_id = jsonl_path.stem
    session_dir = jsonl_path.parent / session_id

    # Main JSONL
    _hash_file(h, jsonl_path)

    # Subagent files
    subagents_dir = session_dir / "subagents"
    if subagents_dir.is_dir():
        for f in sorted(subagents_dir.iterdir()):
            if f.is_file():
                _hash_file(h, f)

    # Tool-result sidecars
    tool_results_dir = session_dir / "tool-results"
    if tool_results_dir.is_dir():
        for f in sorted(tool_results_dir.iterdir()):
            if f.is_file():
                _hash_file(h, f)

    return h.hexdigest()


def _hash_file(h: hashlib._Hash, path: Path) -> None:
    """Hash file name + size + first/last 4KB into the hasher."""
    size = path.stat().st_size
    h.update(path.name.encode())
    h.update(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(4096))
        if size > 4096:
            f.seek(max(0, size - 4096))
            h.update(f.read(4096))
