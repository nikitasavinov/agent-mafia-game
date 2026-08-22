"""The `mafia doctor` command."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from mafia_game.agents import run_preflight

from .common import console, load_config, resolve_config_path


def register(app: typer.Typer) -> None:
    app.command()(doctor)


def doctor(
    config: Path | None = typer.Argument(
        None,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Roster TOML; omit it to use the same default as `mafia play`.",
    ),
    live: bool = typer.Option(
        False,
        "--live",
        help="Send one minimal provider-backed probe per distinct model.",
    ),
) -> None:
    """Validate the roster, endpoints, authentication, and optional models."""
    settings = load_config(resolve_config_path(config))
    if not asyncio.run(run_preflight(settings, live=live, console=console)):
        raise typer.Exit(1)
