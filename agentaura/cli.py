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


@app.command()
def status(
    claude_dir: Annotated[
        Path | None,
        typer.Option("--claude-dir", help="Path to ~/.claude directory"),
    ] = None,
) -> None:
    """Show discovered sessions and import status."""
    from agentaura.adapters.claude_code.parser import discover_sessions
    from agentaura.pipeline.state import ImportState

    sessions = discover_sessions(claude_dir)
    state = ImportState(claude_dir=claude_dir)
    stats = state.get_stats()
    state.close()

    console.print("\n[bold]Claude Code Sessions[/bold]")
    console.print(f"  Discovered:  {len(sessions)}")
    console.print(f"  Imported:    {stats['imported_sessions']}")
    console.print(f"  Remaining:   {len(sessions) - stats['imported_sessions']}")
    console.print(f"  Total cost:  ${stats['total_cost_usd']:.4f}")
    console.print()


@app.command(name="import")
def import_sessions(
    project: Annotated[
        str | None,
        typer.Option("--project", "-p", help="Import only sessions for this project path"),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-n", help="Maximum number of sessions to import"),
    ] = None,
    host: Annotated[
        str,
        typer.Option("--host", help="Langfuse host URL"),
    ] = "http://localhost:3000",
    public_key: Annotated[
        str,
        typer.Option("--public-key", envvar="LANGFUSE_PUBLIC_KEY", help="Langfuse public key"),
    ] = "pk-lf-agentaura",
    secret_key: Annotated[
        str,
        typer.Option("--secret-key", envvar="LANGFUSE_SECRET_KEY", help="Langfuse secret key"),
    ] = "sk-lf-agentaura",
    claude_dir: Annotated[
        Path | None,
        typer.Option("--claude-dir", help="Path to ~/.claude directory"),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-v", help="Enable verbose logging"),
    ] = False,
) -> None:
    """Import Claude Code sessions into Langfuse."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
    )

    from agentaura.pipeline.exporter import create_langfuse_client, import_all
    from agentaura.pipeline.state import ImportState

    langfuse = create_langfuse_client(
        public_key=public_key,
        secret_key=secret_key,
        host=host,
    )

    if not langfuse.auth_check():
        console.print("[red]Error:[/red] Failed to authenticate with Langfuse")
        console.print(f"  Host: {host}")
        console.print(f"  Public key: {public_key}")
        raise typer.Exit(1)

    state = ImportState(claude_dir=claude_dir)

    console.print("\n[bold]Importing Claude Code sessions → Langfuse[/bold]")
    console.print(f"  Host: {host}")
    if project:
        console.print(f"  Project filter: {project}")
    if limit:
        console.print(f"  Limit: {limit}")
    console.print()

    imported, skipped, total_cost = import_all(
        langfuse, state, claude_dir=claude_dir, project_filter=project, limit=limit
    )

    state.close()

    console.print()
    table = Table(title="Import Summary")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Imported", str(imported))
    table.add_row("Skipped", str(skipped))
    table.add_row("Total cost", f"${total_cost:.4f}")
    console.print(table)
    console.print()


@app.command()
def watch(
    host: Annotated[
        str,
        typer.Option("--host", help="Langfuse host URL"),
    ] = "http://localhost:3000",
    public_key: Annotated[
        str,
        typer.Option("--public-key", envvar="LANGFUSE_PUBLIC_KEY", help="Langfuse public key"),
    ] = "pk-lf-agentaura",
    secret_key: Annotated[
        str,
        typer.Option("--secret-key", envvar="LANGFUSE_SECRET_KEY", help="Langfuse secret key"),
    ] = "sk-lf-agentaura",
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

    from agentaura.adapters.claude_code.mapper import export_session
    from agentaura.adapters.claude_code.parser import parse_session
    from agentaura.core.normalized import normalize_session
    from agentaura.pipeline.exporter import create_langfuse_client
    from agentaura.watcher.file_watcher import SessionWatcher

    langfuse = create_langfuse_client(
        public_key=public_key,
        secret_key=secret_key,
        host=host,
    )

    if not langfuse.auth_check():
        console.print("[red]Error:[/red] Failed to authenticate with Langfuse")
        raise typer.Exit(1)

    def on_session_ready(jsonl_path: Path) -> None:
        parsed = parse_session(jsonl_path)
        normalized = normalize_session(parsed)
        if not normalized.turns and not normalized.subagents:
            return
        export_session(langfuse, normalized)
        langfuse.flush()
        logger = logging.getLogger(__name__)
        logger.info(
            "Exported %s (%d turns, $%.4f)",
            normalized.session_id[:12],
            len(normalized.turns),
            normalized.total_cost_usd,
        )

    console.print("\n[bold]AgentAura Watcher[/bold]")
    console.print(f"  Langfuse: {host}")
    console.print("  Watching: ~/.claude/projects/")
    console.print("  Press Ctrl+C to stop\n")

    watcher = SessionWatcher(on_session_ready, claude_dir=claude_dir)
    watcher.run_forever()


if __name__ == "__main__":
    app()
