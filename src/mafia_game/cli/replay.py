"""The `mafia replay` command."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import typer

from mafia_game.gameplay import ReplayView
from mafia_game.runs import RunDatabase, render_replay

from .common import DEFAULT_DATABASE, console


def register(app: typer.Typer) -> None:
    app.command()(replay)


def replay(
    run_id: str = typer.Argument(..., help="Run ID from the SQLite database."),
    view: ReplayView = typer.Option(ReplayView.OMNISCIENT, case_sensitive=False),
    step: bool = typer.Option(False, "--step", help="Pause at phase boundaries."),
    delay: float = typer.Option(
        0.0,
        "--delay",
        min=0.0,
        max=60.0,
        help="Wait this many seconds after each visible event.",
    ),
    database: Path = typer.Option(
        DEFAULT_DATABASE,
        "--database",
        "-d",
        dir_okay=False,
        help="SQLite database containing the run.",
    ),
) -> None:
    """Replay a completed or aborted game without contacting any model."""
    try:
        with RunDatabase(database) as run_database:
            metadata = run_database.load_metadata(run_id)
            transactions = run_database.read_transactions(run_id)
    except (FileNotFoundError, OSError, sqlite3.Error, ValueError) as error:
        console.print(f"[red]Cannot load replay: {error}[/red]")
        raise typer.Exit(2) from error
    events = [event for transaction in transactions for event in transaction.events]
    render_replay(metadata, events, view=view, step=step, delay=delay, console=console)
