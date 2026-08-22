"""The `mafia play` command: run one observed game."""

from __future__ import annotations

import asyncio
import secrets
import shlex
from datetime import UTC, datetime
from pathlib import Path

import typer
from pydantic import ValidationError

from mafia_game.agents import HumanDriver, default_drivers, run_preflight
from mafia_game.config import Provider
from mafia_game.gameplay import GameRunner, RichGameObserver
from mafia_game.runs import RunDatabase

from .common import (
    DEFAULT_DATABASE,
    console,
    human_seat,
    load_config,
    resolve_config_path,
    resolve_seed,
)


def register(app: typer.Typer) -> None:
    app.command()(play)


def play(
    config: Path | None = typer.Argument(
        None,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Roster TOML; omit it to use the configured or bundled default.",
    ),
    seed: int | None = typer.Option(
        None,
        min=-1,
        help="Override the configured seed; use -1 for a fresh random seed.",
    ),
    communication_style: str | None = typer.Option(
        None,
        "--communication-style",
        help="Override every automated seat's communication style for this game.",
    ),
    step: bool = typer.Option(False, "--step", help="Pause at every phase boundary."),
    database: Path = typer.Option(
        DEFAULT_DATABASE,
        "--database",
        "-d",
        dir_okay=False,
        help="SQLite database for run history.",
    ),
) -> None:
    """Play one complete game."""
    config_path = resolve_config_path(config)
    settings = load_config(config_path)
    if communication_style is not None:
        try:
            settings = settings.with_communication_style(communication_style)
        except ValidationError as error:
            console.print(f"[red]Invalid communication style: {error}[/red]")
            raise typer.Exit(2) from error
    perspective_seat = human_seat(settings)
    drivers = None
    if perspective_seat is not None:
        drivers = default_drivers()
        drivers[Provider.HUMAN] = HumanDriver(
            console,
            player_names={
                seat: player.name for seat, player in enumerate(settings.players, start=1)
            },
        )
    if not asyncio.run(run_preflight(settings, live=False, console=console, drivers=drivers)):
        doctor_command = "mafia doctor --live"
        if config is not None:
            doctor_command = f"mafia doctor {shlex.quote(str(config_path))} --live"
        console.print("[yellow]For detailed diagnostics, run:[/yellow] " + doctor_command)
        raise typer.Exit(1)
    chosen_seed = resolve_seed(seed, settings.game.seed)
    game_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    try:
        with RunDatabase(database) as run_database:
            store = run_database.create_run(game_id, settings, chosen_seed)
            observer = RichGameObserver(
                console,
                step=step,
                perspective_seat=perspective_seat,
            )
            runner = GameRunner.create(
                settings,
                store,
                chosen_seed,
                observer=observer,
                drivers=drivers,
            )
            observer.show_roster(runner.state)
            console.print(f"[dim]Seed: {chosen_seed} · Database: {database}[/dim]")
            asyncio.run(runner.run())
    except KeyboardInterrupt:
        console.print("[red]Game aborted by operator.[/red]")
        raise typer.Exit(130) from None
    except Exception as error:
        console.print(f"[red]Game aborted: {type(error).__name__}: {error}[/red]")
        raise typer.Exit(1) from error
    console.print(f"[dim]Replay with:[/dim] uv run mafia replay {game_id}")
