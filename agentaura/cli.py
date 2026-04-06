"""AgentAura CLI — monitoring dashboard for coding agents."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="agentaura", help="Monitoring dashboard for coding agents.")
console = Console()


def _get_adapters(agent: str) -> list[tuple[str, object]]:
    """Return (name, adapter) pairs for the selected agent(s)."""
    from agentaura.adapters.claude_code.adapter import ClaudeCodeAdapter
    from agentaura.adapters.codex.adapter import CodexAdapter

    adapters: dict[str, object] = {
        "claude": ClaudeCodeAdapter(),
        "codex": CodexAdapter(),
    }
    if agent == "all":
        return list(adapters.items())
    return [(agent, adapters[agent])]


@app.command()
def status(
    agent: Annotated[
        str,
        typer.Option("--agent", "-a", help="Agent to query: claude, codex, all"),
    ] = "all",
) -> None:
    """Show discovered sessions and import status."""
    from agentaura.adapters.base import AgentAdapter
    from agentaura.pipeline.state import ImportState

    state = ImportState()
    stats = state.get_stats()

    console.print("\n[bold]AgentAura Status[/bold]")

    for _name, adapter in _get_adapters(agent):
        assert isinstance(adapter, AgentAdapter)
        sessions = adapter.discover_sessions()
        console.print(f"\n  [bold]{adapter.agent_name}[/bold]: {len(sessions)} sessions")

    console.print(f"\n  Imported:  {stats['imported_sessions']}")
    console.print(f"  Total cost: ${stats['total_cost_usd']:.4f}")
    console.print()

    state.close()


@app.command(name="import")
def import_sessions(
    agent: Annotated[
        str,
        typer.Option("--agent", "-a", help="Agent to import: claude, codex, all"),
    ] = "all",
    project: Annotated[
        str | None,
        typer.Option("--project", "-p", help="Import only sessions for this project path"),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-n", help="Maximum number of sessions to import per agent"),
    ] = None,
    otel_endpoint: Annotated[
        str,
        typer.Option("--otel-endpoint", help="OTel Collector HTTP endpoint"),
    ] = "http://localhost:4318",
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable verbose logging"),
    ] = False,
) -> None:
    """Import coding agent sessions into Langfuse."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
    )

    from agentaura.adapters.base import AgentAdapter
    from agentaura.adapters.claude_code.mapper import export_session, flush
    from agentaura.pipeline.state import ImportState, session_checksum

    state = ImportState()
    log = logging.getLogger(__name__)

    console.print("\n[bold]Importing sessions → Langfuse[/bold]")
    console.print(f"  OTel endpoint: {otel_endpoint}")
    console.print(f"  Agent: {agent}")
    if project:
        console.print(f"  Project filter: {project}")
    console.print()

    total_imported = 0
    total_skipped = 0
    total_cost = 0.0

    for _name, adapter in _get_adapters(agent):
        assert isinstance(adapter, AgentAdapter)
        session_paths = adapter.discover_sessions()

        # Filter by project — path-based for Claude, post-parse for Codex
        if project and adapter.agent_name == "Claude Code":
            encoded = project.replace("/", "-").lstrip("-")
            encoded = f"-{encoded}"
            session_paths = [p for p in session_paths if project in str(p) or encoded in str(p)]
        elif project:
            # For non-Claude agents, filter after parsing by checking normalized cwd
            filtered: list[Path] = []
            for p in session_paths:
                try:
                    ns = adapter.parse_and_normalize(p)
                    if ns.cwd and project in ns.cwd:
                        filtered.append(p)
                except Exception:
                    pass
            session_paths = filtered

        if limit is not None:
            session_paths = session_paths[:limit]

        imported = 0
        skipped = 0
        pending: list[tuple[str, Path, str, int, float]] = []

        for i, path in enumerate(session_paths):
            sid = path.stem
            cs = session_checksum(path)

            if state.is_imported(sid, cs):
                skipped += 1
                continue

            try:
                normalized = adapter.parse_and_normalize(path)
                if not normalized.turns and not normalized.subagents:
                    state.mark_imported(sid, path, cs, event_count=0, cost_usd=0.0)
                    skipped += 1
                    continue

                export_session(normalized, endpoint=otel_endpoint)
                ec = normalized.total_generations + normalized.total_tool_calls
                pending.append((sid, path, cs, ec, normalized.total_cost_usd))
                imported += 1
                total_cost += normalized.total_cost_usd

                log.info(
                    "[%s] Imported %s (%d turns, $%.4f)",
                    adapter.agent_name,
                    sid[:12],
                    len(normalized.turns),
                    normalized.total_cost_usd,
                )
            except Exception:
                log.exception("[%s] Failed to import %s", adapter.agent_name, path)
                skipped += 1

            if (i + 1) % 20 == 0 and pending:
                if flush(otel_endpoint):
                    for s, p, c, e, cost in pending:
                        state.mark_imported(s, p, c, event_count=e, cost_usd=cost)
                else:
                    log.warning("Flush failed — %d sessions not marked as imported", len(pending))
                    total_imported -= len(pending)
                    total_skipped += len(pending)
                pending.clear()

        if pending:
            if flush(otel_endpoint):
                for s, p, c, e, cost in pending:
                    state.mark_imported(s, p, c, event_count=e, cost_usd=cost)
            else:
                log.warning("Flush failed — %d sessions not marked as imported", len(pending))
                total_imported -= len(pending)
                total_skipped += len(pending)

        total_imported += imported
        total_skipped += skipped

    state.close()

    console.print()
    table = Table(title="Import Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Imported", str(total_imported))
    table.add_row("Skipped", str(total_skipped))
    table.add_row("Total cost", f"${total_cost:.4f}")
    console.print(table)
    console.print()


@app.command()
def watch(
    otel_endpoint: Annotated[
        str,
        typer.Option("--otel-endpoint", help="OTel Collector HTTP endpoint"),
    ] = "http://localhost:4318",
    claude_dir: Annotated[
        Path | None,
        typer.Option("--claude-dir", help="Path to ~/.claude directory"),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable verbose logging"),
    ] = False,
) -> None:
    """Watch for live Claude Code sessions and export to Langfuse."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
    )

    from agentaura.adapters.claude_code.mapper import export_session, flush
    from agentaura.adapters.claude_code.parser import parse_session
    from agentaura.core.normalized import normalize_session
    from agentaura.watcher.file_watcher import SessionWatcher

    def on_session_ready(jsonl_path: Path) -> None:
        parsed = parse_session(jsonl_path)
        normalized = normalize_session(parsed)
        if not normalized.turns and not normalized.subagents:
            return
        export_session(normalized, endpoint=otel_endpoint)
        flush(otel_endpoint)
        log = logging.getLogger(__name__)
        log.info(
            "Exported %s (%d turns, $%.4f)",
            normalized.session_id[:12],
            len(normalized.turns),
            normalized.total_cost_usd,
        )

    console.print("\n[bold]AgentAura Watcher[/bold]")
    console.print(f"  OTel endpoint: {otel_endpoint}")
    console.print("  Watching: ~/.claude/projects/")
    console.print("  Press Ctrl+C to stop\n")

    watcher = SessionWatcher(on_session_ready, claude_dir=claude_dir)
    watcher.run_forever()


if __name__ == "__main__":
    app()
