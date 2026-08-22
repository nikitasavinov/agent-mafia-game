"""The observer hook the engine reports progress through.

Observers are strictly read-only and must never influence play, which lets the
terminal UI, replay, and batch progress reporting share one interface.
"""

from __future__ import annotations

from typing import Protocol

from mafia_game.agents import RequestKind

from .models import GameEvent, GameState


class GameObserver(Protocol):
    def on_events(self, events: list[GameEvent]) -> None: ...

    def on_thinking(self, state: GameState, seat: int, kind: RequestKind) -> None: ...

    def on_locked(self, state: GameState, seat: int, kind: RequestKind) -> None: ...

    async def phase_boundary(self, state: GameState) -> None: ...


class NullObserver:
    def on_events(self, events: list[GameEvent]) -> None:
        del events

    def on_thinking(self, state: GameState, seat: int, kind: RequestKind) -> None:
        del state, seat, kind

    def on_locked(self, state: GameState, seat: int, kind: RequestKind) -> None:
        del state, seat, kind

    async def phase_boundary(self, state: GameState) -> None:
        del state
