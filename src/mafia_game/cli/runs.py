"""The `mafia runs` command group: list and search."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import typer
from rich import box
from rich.table import Table
from rich.text import Text

from mafia_game.gameplay import GameStatus, Team
from mafia_game.runs import RunDatabase

from .common import DEFAULT_DATABASE, console, datetime_filter


def register(app: typer.Typer) -> None:
    app.command("list")(list_runs)
    app.command("search")(search_runs)


def list_runs(
    database: Path = typer.Option(
        DEFAULT_DATABASE, "--database", "-d", dir_okay=False, help="SQLite run database."
    ),
    status: GameStatus | None = typer.Option(None, case_sensitive=False),
    winner: Team | None = typer.Option(None, case_sensitive=False),
    model: str | None = typer.Option(None, help="Filter by a model name substring."),
    player: str | None = typer.Option(None, help="Filter by a player name substring."),
    since: datetime | None = typer.Option(None, help="Only runs created at or after this time."),
    limit: int = typer.Option(50, min=1, max=1_000),
) -> None:
    """List recorded runs using indexed metadata filters."""
    try:
        with RunDatabase(database) as run_database:
            records = run_database.list_runs(
                status=status.value if status is not None else None,
                winner=winner.value if winner is not None else None,
                model=model,
                player=player,
                since=datetime_filter(since),
                limit=limit,
            )
    except (OSError, sqlite3.Error, ValueError) as error:
        console.print(f"[red]Cannot list runs: {error}[/red]")
        raise typer.Exit(2) from error
    if not records:
        console.print("[yellow]No matching runs.[/yellow]")
        return
    table = Table(title="Mafia runs", box=box.SIMPLE_HEAVY, header_style="bold")
    table.add_column("Run", no_wrap=True)
    table.add_column("Created", no_wrap=True)
    table.add_column("Result")
    table.add_column("Events", justify="right")
    for record in records:
        table.add_row(
            record.game_id,
            record.created_at[:10],
            record.status + (f"/{record.winner}" if record.winner else ""),
            str(record.event_count),
        )
    console.print(table)


def search_runs(
    query: str = typer.Argument(..., help="Exact phrase to find in recorded event messages."),
    database: Path = typer.Option(
        DEFAULT_DATABASE, "--database", "-d", dir_okay=False, help="SQLite run database."
    ),
    include_private: bool = typer.Option(
        False,
        "--include-private",
        help="Include private, omniscient, and internal event messages.",
    ),
    limit: int = typer.Option(50, min=1, max=1_000),
) -> None:
    """Full-text search recorded run messages; public events are the default."""
    try:
        with RunDatabase(database) as run_database:
            matches = run_database.search(
                query,
                include_private=include_private,
                limit=limit,
            )
    except (OSError, sqlite3.Error, ValueError) as error:
        console.print(f"[red]Cannot search runs: {error}[/red]")
        raise typer.Exit(2) from error
    if not matches:
        console.print("[yellow]No matching events.[/yellow]")
        return
    table = Table(title=f'Search · "{query}"', box=box.SIMPLE_HEAVY, header_style="bold")
    table.add_column("Run", min_width=22, no_wrap=True)
    table.add_column("Event", no_wrap=True)
    table.add_column("Message", overflow="fold", ratio=3)
    for match in matches:
        period = f"D{match.day}" if match.day is not None else f"N{match.night or 1}"
        table.add_row(
            match.game_id,
            f"{period}/{match.event_type}",
            Text(_compact(match.message, 240)),
        )
    console.print(table)


def _compact(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"
