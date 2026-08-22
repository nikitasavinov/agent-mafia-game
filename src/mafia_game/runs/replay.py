"""Rendering a recorded run back to the terminal.

Replay reuses the live observer, so a replay looks like the original game and
obeys the same visibility rules — no model is contacted.
"""

from __future__ import annotations

from collections.abc import Iterable
from time import sleep

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from mafia_game.gameplay.console import RichGameObserver
from mafia_game.gameplay.event_payloads import optional_int
from mafia_game.gameplay.models import GameEvent, GameState, PlayerState, Role
from mafia_game.gameplay.visibility import ReplayView, visible_for_replay

from .models import RunMetadata


def render_replay(
    metadata: RunMetadata,
    events: Iterable[GameEvent],
    *,
    view: ReplayView,
    step: bool,
    delay: float = 0.0,
    console: Console | None = None,
) -> None:
    output = console or Console()
    recorded_events = list(events)
    winner = metadata.winner.value if metadata.winner else "none"
    winner_style = "bright_magenta" if winner == "mafia" else "bright_cyan"

    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold dim", justify="right")
    summary.add_column()
    summary.add_row("Run", metadata.game_id)
    summary.add_row("Status", metadata.status.value)
    summary.add_row("Winner", Text(winner.upper(), style=f"bold {winner_style}"))
    summary.add_row("Seed", str(metadata.seed))
    summary.add_row("View", Text(view.value.upper(), style="bold bright_yellow"))
    summary.add_row("Players", str(len(metadata.config.players)))
    output.print(
        Panel(
            summary,
            title=Text("Mafia Replay", style="bold bright_cyan"),
            title_align="left",
            border_style="cyan",
            padding=(0, 2),
            expand=False,
        )
    )
    output.line()

    player_names = {
        seat: player.name for seat, player in enumerate(metadata.config.players, start=1)
    }
    observer = RichGameObserver(output, player_names=player_names)
    roster = _reconstruct_initial_state(metadata, recorded_events)
    roster_rendered = False
    if view is ReplayView.OMNISCIENT and roster is not None:
        observer.show_roster(
            roster,
            title=f"ROLES REVEALED  ·  {metadata.game_id}",
        )
        roster_rendered = True
        if delay:
            sleep(delay)

    for event in recorded_events:
        if not visible_for_replay(event, view):
            continue
        if roster_rendered and event.type == "role_assigned":
            continue
        observer.render_event(event)
        if delay:
            sleep(delay)
        if step and event.type == "phase_started":
            output.input("[bold cyan]Next phase[/bold cyan] [dim]· press Enter…[/dim] ")


def _reconstruct_initial_state(
    metadata: RunMetadata,
    events: list[GameEvent],
) -> GameState | None:
    """Build the opening roster when a recording contains every role reveal."""
    roles: dict[int, Role] = {}
    for event in events:
        if event.type != "role_assigned" or not visible_for_replay(event, ReplayView.OMNISCIENT):
            continue
        seat = optional_int(event.data.get("seat"))
        raw_role = event.data.get("role")
        if seat is None or not isinstance(raw_role, str):
            continue
        try:
            roles[seat] = Role(raw_role)
        except ValueError:
            continue

    expected_seats = set(range(1, len(metadata.config.players) + 1))
    if set(roles) != expected_seats:
        return None

    players = [
        PlayerState(
            seat=seat,
            name=player.name,
            provider=player.provider,
            model=player.model,
            role=roles[seat],
            reasoning_effort=player.reasoning_effort,
            communication_style=player.communication_style,
        )
        for seat, player in enumerate(metadata.config.players, start=1)
    ]
    return GameState(
        game_id=metadata.game_id,
        seed=metadata.seed,
        players=players,
    )
