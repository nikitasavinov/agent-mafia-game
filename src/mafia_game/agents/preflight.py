"""The `mafia doctor` check: can this roster actually play?

Preflight answers that before a game starts spending quota, so a missing
credential or unreachable endpoint surfaces as a table rather than as a dead
seat on Night 1.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table

from mafia_game.config import AppConfig, PlayerConfig, Provider

from .contracts import DriverCheck, PlayerDriver
from .registry import default_drivers


@dataclass(frozen=True, slots=True)
class PreflightRow:
    """One driver check, labelled with the runtime it was run against."""

    provider: str
    model: str
    effort: str
    check: DriverCheck


async def run_preflight(
    config: AppConfig,
    *,
    live: bool,
    drivers: dict[Provider, PlayerDriver] | None = None,
    console: Console | None = None,
) -> bool:
    """Check every distinct runtime in `config` and print the results.

    Returns True only if every check passed. With `live=True` each runtime is
    also asked for one real model response.
    """
    rows = await _collect_rows(config, drivers or default_drivers(), live=live)
    _render(rows, console or Console())
    return all(row.check.ok for row in rows)


async def _collect_rows(
    config: AppConfig,
    drivers: dict[Provider, PlayerDriver],
    *,
    live: bool,
) -> list[PreflightRow]:
    rows: list[PreflightRow] = []
    seen: set[tuple[str, ...]] = set()
    with tempfile.TemporaryDirectory(prefix="mafia-doctor-") as temp_dir:
        root = Path(temp_dir)
        for player in config.players:
            runtime = _runtime_identity(player)
            if runtime in seen:
                continue
            seen.add(runtime)
            # Each check gets a throwaway workspace so a provider that writes
            # state cannot contaminate the next one, or the real run.
            workspace = root / f"{player.provider.value}-{len(seen)}"
            workspace.mkdir()
            checks = await drivers[player.provider].preflight(player, workspace, live=live)
            rows.extend(
                PreflightRow(
                    provider=player.provider.value,
                    model=player.model,
                    effort=(
                        player.reasoning_effort.value
                        if player.reasoning_effort is not None
                        else "—"
                    ),
                    check=check,
                )
                for check in checks
            )
    return rows


def _runtime_identity(player: PlayerConfig) -> tuple[str, ...]:
    """Identify the runtime a player talks to, so a roster is checked once per runtime.

    Six players sharing one model and one credential are one thing to verify,
    not six. Inline keys are hashed rather than compared directly so the secret
    never reaches a data structure that might be logged.
    """
    if player.api_key is not None:
        secret = player.api_key.get_secret_value().encode()
        auth = "inline:" + hashlib.sha256(secret).hexdigest()
    else:
        auth = f"env:{player.api_key_env}"
    return (
        player.provider.value,
        player.model,
        player.reasoning_effort.value if player.reasoning_effort is not None else "",
        player.base_url or "",
        auth,
    )


def _render(rows: list[PreflightRow], console: Console) -> None:
    table = Table(title="Mafia doctor")
    table.add_column("Provider")
    table.add_column("Model")
    table.add_column("Effort")
    table.add_column("Check")
    table.add_column("Result")
    table.add_column("Detail", overflow="fold")
    for row in rows:
        table.add_row(
            row.provider,
            row.model,
            row.effort,
            row.check.name,
            "[green]PASS[/green]" if row.check.ok else "[red]FAIL[/red]",
            row.check.detail,
        )
    console.print(table)
