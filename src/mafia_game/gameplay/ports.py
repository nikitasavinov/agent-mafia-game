"""The persistence boundary the gameplay engine depends on.

Declared as protocols here, and implemented in `mafia_game.runs`, so the rules
engine never imports a database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .models import GameEvent, GameStatus, Team


class CommitEvents(Protocol):
    def __call__(
        self,
        events: list[GameEvent],
        transition_id: str | None = None,
    ) -> None: ...


class GameStore(Protocol):
    """Persistence boundary required by the gameplay engine."""

    run_dir: Path

    @property
    def game_id(self) -> str: ...

    def append(self, events: list[GameEvent], transition_id: str | None = None) -> object: ...

    def finish(self, status: GameStatus, winner: Team | None = None) -> None: ...
