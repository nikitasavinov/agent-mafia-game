"""Who is allowed to read which events.

This is the privacy boundary of the game: prompts, live rendering, and replays
all filter through the predicates here, so a leak has exactly one place to hide.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from .models import Audience, GameEvent, GameState, Role


class ReplayView(StrEnum):
    """How much of a recorded run to reveal when replaying it."""

    OMNISCIENT = "omniscient"
    PUBLIC = "public"


def visible_to_player(event: GameEvent, state: GameState, seat: int) -> bool:
    """Report whether `seat` may see `event` while the game is being played."""
    visibility = event.visibility
    match visibility.audience:
        case Audience.PUBLIC:
            return True
        case Audience.PLAYER:
            return visibility.seat == seat
        case Audience.MAFIA:
            return state.player(seat).role is Role.MAFIA
        case Audience.OMNISCIENT | Audience.INTERNAL:
            return False


def visible_for_replay(event: GameEvent, view: ReplayView) -> bool:
    """Report whether `event` belongs in a replay rendered at `view`.

    Internal events are engine diagnostics — raw provider traffic, timeouts,
    rejected actions — and are never rendered at any view.
    """
    if event.visibility.audience is Audience.INTERNAL:
        return False
    if view is ReplayView.OMNISCIENT:
        return True
    return event.visibility.audience is Audience.PUBLIC


def event_history_lines(events: Iterable[GameEvent], state: GameState, seat: int) -> list[str]:
    """Render the slice of `events` that `seat` may read, ready for a prompt."""
    return [event.message for event in events if visible_to_player(event, state, seat)]
