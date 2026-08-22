"""Persistence-facing shapes: stored transactions, run metadata, and query rows.

The Pydantic models here define the on-disk format and are versioned with
`schema_version`. The dataclasses are read models, built from SQLite rows.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel

from mafia_game.config import AppConfig
from mafia_game.gameplay.models import GameEvent, GameStatus, Team, Visibility


class GameTransaction(BaseModel):
    schema_version: Literal[1] = 1
    game_id: str
    sequence: int
    transition_id: str
    created_at: str
    events: list[GameEvent]


class RunMetadata(BaseModel):
    schema_version: Literal[1] = 1
    game_id: str
    created_at: str
    updated_at: str
    seed: int
    status: GameStatus
    winner: Team | None = None
    config: AppConfig


@dataclass(frozen=True, slots=True)
class RunRecord:
    game_id: str
    created_at: str
    updated_at: str
    status: str
    winner: str | None
    seed: int
    day: int
    night: int
    duration_seconds: float | None
    event_count: int
    player_count: int
    models: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SearchResult:
    game_id: str
    created_at: str
    event_type: str
    visibility: Visibility
    message: str
    day: int | None
    night: int | None


@dataclass(slots=True)
class StatisticsRow:
    label: str
    appearances: int = 0
    wins: int = 0
    town_appearances: int = 0
    town_wins: int = 0
    mafia_appearances: int = 0
    mafia_wins: int = 0
    requests: int = 0
    failures: int = 0
    usage: defaultdict[str, float] = field(default_factory=lambda: defaultdict(float))

    @property
    def win_rate(self) -> float:
        return self.wins / self.appearances if self.appearances else 0.0

    @property
    def failure_rate(self) -> float:
        return self.failures / self.requests if self.requests else 0.0

    @property
    def town_win_rate(self) -> float:
        return self.town_wins / self.town_appearances if self.town_appearances else 0.0

    @property
    def mafia_win_rate(self) -> float:
        return self.mafia_wins / self.mafia_appearances if self.mafia_appearances else 0.0
