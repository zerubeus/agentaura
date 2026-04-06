"""AgentAura CLI — monitoring dashboard for coding agents."""

from __future__ import annotations

import typer

app = typer.Typer(name="agentaura", help="Monitoring dashboard for coding agents.")


@app.command()
def status() -> None:
    """Show discovered sessions and import status."""
    from agentaura.adapters.claude_code.parser import discover_sessions

    sessions = discover_sessions()
    typer.echo(f"Discovered {len(sessions)} Claude Code sessions")


if __name__ == "__main__":
    app()
