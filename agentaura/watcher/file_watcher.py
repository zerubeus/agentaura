"""File watcher for live Claude Code session import.

Monitors ~/.claude/projects/ for JSONL file changes using watchdog.
When a session file is modified (appended to), the full session is
re-parsed and re-exported to Langfuse. This is a fallback for when
Claude Code's native OTel export is not enabled.

Design:
- Debounces rapid writes (JSONL files get many appends per session)
- Only processes top-level session JSONLs, not subagent files
- Re-exports the full session on each change (not incremental)
- Uses the same import state for idempotency
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

# Debounce: wait this many seconds after last modification before processing
DEBOUNCE_SECONDS = 5.0


class _SessionEventHandler(FileSystemEventHandler):
    """Handles JSONL file modification events with debouncing."""

    def __init__(self, on_session_ready: callable) -> None:  # type: ignore[type-arg]
        super().__init__()
        self._on_session_ready = on_session_ready
        self._pending: dict[str, float] = {}  # path -> last_modified_time
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._schedule_check()

    def on_modified(self, event: FileModifiedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if not path.suffix == ".jsonl":
            return
        # Skip subagent files
        if "subagents" in path.parts:
            return

        with self._lock:
            self._pending[str(path)] = time.monotonic()

    def _schedule_check(self) -> None:
        """Schedule periodic check for debounced files."""
        self._timer = threading.Timer(1.0, self._check_pending)
        self._timer.daemon = True
        self._timer.start()

    def _check_pending(self) -> None:
        """Process files that haven't been modified for DEBOUNCE_SECONDS."""
        now = time.monotonic()
        ready: list[str] = []

        with self._lock:
            for path_str, last_mod in list(self._pending.items()):
                if now - last_mod >= DEBOUNCE_SECONDS:
                    ready.append(path_str)
                    del self._pending[path_str]

        for path_str in ready:
            try:
                self._on_session_ready(Path(path_str))
            except Exception:
                logger.exception("Error processing %s", path_str)

        self._schedule_check()

    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()


class SessionWatcher:
    """Watches Claude Code projects directory for session changes."""

    def __init__(
        self,
        on_session_ready: callable,  # type: ignore[type-arg]
        claude_dir: Path | None = None,
    ):
        self._claude_dir = claude_dir or Path.home() / ".claude"
        self._projects_dir = self._claude_dir / "projects"
        self._handler = _SessionEventHandler(on_session_ready)
        self._observer = Observer()

    def start(self) -> None:
        """Start watching (non-blocking)."""
        if not self._projects_dir.is_dir():
            logger.error("Projects directory not found: %s", self._projects_dir)
            return

        self._observer.schedule(self._handler, str(self._projects_dir), recursive=True)
        self._observer.start()
        logger.info("Watching %s for session changes...", self._projects_dir)

    def stop(self) -> None:
        """Stop watching."""
        self._handler.stop()
        self._observer.stop()
        self._observer.join(timeout=5)

    def run_forever(self) -> None:
        """Start watching and block until interrupted."""
        self.start()
        try:
            while self._observer.is_alive():
                self._observer.join(timeout=1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
