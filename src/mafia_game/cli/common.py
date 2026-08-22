"""Helpers shared by the CLI commands: config loading, seeds, and the console.

Configuration errors are reported and turned into a non-zero exit here so each
command does not have to repeat the same error handling.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import typer
from pydantic import ValidationError
from rich.console import Console

from mafia_game.config import AppConfig, Provider

console = Console()
DEFAULT_DATABASE = Path("mafia.db")
MAFIA_CONFIG_ENV = "MAFIA_CONFIG"
BUNDLED_CONFIG_PATH = Path(__file__).parents[1] / "presets" / "openrouter.toml"


def resolve_config_path(explicit: Path | None) -> Path:
    """Resolve the roster path from the CLI, environment, project, or user config."""
    if explicit is not None:
        return explicit.expanduser()

    configured = os.getenv(MAFIA_CONFIG_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()

    project_config = Path.cwd() / "mafia.toml"
    if project_config.is_file():
        return project_config

    xdg_root = os.getenv("XDG_CONFIG_HOME", "").strip()
    config_root = Path(xdg_root).expanduser() if xdg_root else Path.home() / ".config"
    user_config = config_root / "mafia" / "config.toml"
    if user_config.is_file():
        return user_config

    return BUNDLED_CONFIG_PATH


def resolve_seed(override: int | None, configured: int | None) -> int:
    requested = override if override is not None else configured
    if requested is None or requested == -1:
        return secrets.randbits(63)
    return requested


def datetime_filter(value: datetime | None) -> str | None:
    """Render a CLI timestamp as the UTC ISO string the run database stores."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def load_config(path: Path) -> AppConfig:
    try:
        return AppConfig.load(path)
    except FileNotFoundError:
        console.print(f"[red]Configuration not found: {path}[/red]")
    except (OSError, ValidationError, ValueError) as error:
        console.print(f"[red]Invalid configuration: {error}[/red]")
    raise typer.Exit(2)


def human_seat(config: AppConfig) -> int | None:
    return next(
        (
            seat
            for seat, player in enumerate(config.players, start=1)
            if player.provider is Provider.HUMAN
        ),
        None,
    )


def reject_human_players(config: AppConfig, command: str) -> None:
    if human_seat(config) is None:
        return
    console.print(
        f"[red]Human players are TUI-only; use `mafia play`, not `mafia {command}`.[/red]"
    )
    raise typer.Exit(2)
