"""The `mafia stats` command: aggregate results across recorded runs."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import typer
from rich import box
from rich.table import Table

from mafia_game.runs import RunDatabase, StatisticsRow

from .common import DEFAULT_DATABASE, console, datetime_filter


class StatsGroup(StrEnum):
    MODEL = "model"
    PLAYER = "player"
    PROVIDER = "provider"
    FACTION = "faction"


def register(app: typer.Typer) -> None:
    app.command()(stats)


def stats(
    group_by: StatsGroup = typer.Option(
        StatsGroup.MODEL,
        "--group-by",
        case_sensitive=False,
        help="Aggregate by model, player, provider, or faction.",
    ),
    database: Path = typer.Option(
        DEFAULT_DATABASE, "--database", "-d", dir_okay=False, help="SQLite run database."
    ),
    since: datetime | None = typer.Option(None, help="Only runs created at or after this time."),
    model: str | None = typer.Option(None, help="Filter appearances by model substring."),
    player: str | None = typer.Option(None, help="Filter appearances by player substring."),
) -> None:
    """Aggregate outcomes and provider usage across all completed runs."""
    try:
        with RunDatabase(database) as run_database:
            rows = run_database.statistics(
                group_by.value,
                since=datetime_filter(since),
                model=model,
                player=player,
            )
    except (OSError, sqlite3.Error, ValueError) as error:
        console.print(f"[red]Cannot calculate statistics: {error}[/red]")
        raise typer.Exit(2) from error
    if not rows or not any(row.appearances for row in rows):
        console.print("[yellow]No completed runs match these filters.[/yellow]")
        return
    if group_by is StatsGroup.FACTION:
        _render_faction_statistics(rows)
    else:
        _render_outcome_statistics(rows, group_by)


def _render_faction_statistics(rows: list[StatisticsRow]) -> None:
    table = Table(title="Cross-run faction statistics", box=box.SIMPLE_HEAVY)
    table.add_column("Faction")
    table.add_column("Wins", justify="right")
    table.add_column("Games", justify="right")
    table.add_column("Win rate", justify="right")
    for row in sorted(rows, key=lambda item: -item.win_rate):
        table.add_row(row.label, str(row.wins), str(row.appearances), f"{row.win_rate:.1%}")
    console.print(table)


def _render_outcome_statistics(rows: list[StatisticsRow], group_by: StatsGroup) -> None:
    table = Table(
        title=f"Cross-run statistics · by {group_by.value}",
        box=box.SIMPLE_HEAVY,
        header_style="bold",
    )
    table.add_column(group_by.value.title(), min_width=18, no_wrap=True)
    table.add_column("Record", justify="right")
    table.add_column("Town / Mafia", justify="right")
    table.add_column("Failures", justify="right")
    table.add_column("Tokens", justify="right")
    for row in rows:
        table.add_row(
            row.label,
            f"{row.wins}/{row.appearances} · {row.win_rate:.1%}",
            f"{_split_statistics(row.town_wins, row.town_appearances)} / "
            f"{_split_statistics(row.mafia_wins, row.mafia_appearances)}",
            f"{row.failures}/{row.requests}",
            _token_count(row),
        )
    console.print(table)


def _split_statistics(wins: int, appearances: int) -> str:
    return f"{wins}/{appearances}" if appearances else "—"


def _token_count(row: StatisticsRow) -> str:
    total = row.usage.get("total_tokens")
    if total is None:
        total = row.usage.get("input_tokens", 0) + row.usage.get("output_tokens", 0)
    return f"{round(total):,}" if total else "—"
