"""Core domain vocabulary: roles, phases, event visibility, and game state.

Everything here is pure data. The rules that move a game forward live in
`mafia_game.gameplay.rules`, and the rules deciding who may read an event live
in `mafia_game.gameplay.visibility`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, GetCoreSchemaHandler
from pydantic_core import core_schema

from mafia_game.config import Provider, ReasoningEffort


class Role(StrEnum):
    CIVILIAN = "civilian"
    DETECTIVE = "detective"
    DOCTOR = "doctor"
    MAFIA = "mafia"


class Team(StrEnum):
    TOWN = "town"
    MAFIA = "mafia"


class GameStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABORTED = "aborted"


class Phase(StrEnum):
    SETUP = "setup"
    NIGHT_ONE = "night_one"
    DAY_DISCUSSION = "day_discussion"
    DAY_VOTE = "day_vote"
    RUNOFF_DEFENSE = "runoff_defense"
    RUNOFF_VOTE = "runoff_vote"
    DAY_RESOLUTION = "day_resolution"
    MAFIA_DISCUSSION = "mafia_discussion"
    NIGHT_ACTIONS = "night_actions"
    NIGHT_RESOLUTION = "night_resolution"
    ENDED = "ended"
    ABORTED = "aborted"


class Audience(StrEnum):
    """The class of viewer entitled to an event."""

    PUBLIC = "public"
    PLAYER = "player"
    MAFIA = "mafia"
    OMNISCIENT = "omniscient"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class Visibility:
    """Who may read a `GameEvent`.

    Every event carries one of these, and it is the only thing standing between
    a player and information they have not earned. Keeping it a value object
    rather than a bare string means the audiences form a closed set that
    `mafia_game.gameplay.visibility` can match exhaustively, and that a seat
    number can never be lost to a typo in a format string.

    The serialized form is a plain string — `"public"`, `"mafia"`,
    `"omniscient"`, `"internal"`, or `"player:4"` — keeping the SQLite
    representation compact and readable.
    """

    PUBLIC: ClassVar[Visibility]
    MAFIA: ClassVar[Visibility]
    OMNISCIENT: ClassVar[Visibility]
    INTERNAL: ClassVar[Visibility]

    audience: Audience
    seat: int | None = None

    def __post_init__(self) -> None:
        if (self.audience is Audience.PLAYER) != (self.seat is not None):
            raise ValueError("a seat is required for player visibility and forbidden otherwise")

    @classmethod
    def player(cls, seat: int) -> Visibility:
        """Address a single seat, such as a role reveal or a Detective result."""
        return cls(Audience.PLAYER, seat)

    @classmethod
    def parse(cls, raw: object) -> Visibility:
        """Read the serialized form used by stored runs."""
        if isinstance(raw, Visibility):
            return raw
        if not isinstance(raw, str):
            raise TypeError(f"visibility must be a string, got {type(raw).__name__}")
        name, separator, seat = raw.partition(":")
        try:
            audience = Audience(name)
        except ValueError:
            raise ValueError(f"unknown visibility {raw!r}") from None
        if audience is not Audience.PLAYER:
            if separator:
                raise ValueError(f"{audience.value} visibility does not take a seat: {raw!r}")
            return cls(audience)
        if not seat.isdigit():
            raise ValueError(f"player visibility requires a seat number: {raw!r}")
        return cls(audience, int(seat))

    def __str__(self) -> str:
        if self.seat is None:
            return self.audience.value
        return f"{self.audience.value}:{self.seat}"

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source_type: Any, handler: GetCoreSchemaHandler
    ) -> core_schema.CoreSchema:
        del source_type, handler
        return core_schema.no_info_plain_validator_function(
            cls.parse,
            serialization=core_schema.plain_serializer_function_ser_schema(
                str, return_schema=core_schema.str_schema(), when_used="always"
            ),
        )


Visibility.PUBLIC = Visibility(Audience.PUBLIC)
Visibility.MAFIA = Visibility(Audience.MAFIA)
Visibility.OMNISCIENT = Visibility(Audience.OMNISCIENT)
Visibility.INTERNAL = Visibility(Audience.INTERNAL)


class PlayerState(BaseModel):
    seat: int
    name: str
    provider: Provider
    model: str
    role: Role
    reasoning_effort: ReasoningEffort | None = None
    communication_style: str | None = None
    alive: bool = True

    @property
    def team(self) -> Team:
        return Team.MAFIA if self.role is Role.MAFIA else Team.TOWN


class GameState(BaseModel):
    game_id: str
    seed: int
    status: GameStatus = GameStatus.ACTIVE
    phase: Phase = Phase.SETUP
    day: int = 0
    night: int = 1
    players: list[PlayerState]
    day_first_speaker: int | None = None
    detective_investigated: set[int] = Field(default_factory=set)
    doctor_last_target: int | None = None
    winner: Team | None = None

    def player(self, seat: int) -> PlayerState:
        for player in self.players:
            if player.seat == seat:
                return player
        raise ValueError(f"unknown seat {seat}")

    def living(self) -> list[PlayerState]:
        return [player for player in self.players if player.alive]

    def living_seats(self) -> list[int]:
        return [player.seat for player in self.living()]

    def living_role(self, role: Role) -> list[PlayerState]:
        return [player for player in self.living() if player.role is role]


class GameEvent(BaseModel):
    type: str
    visibility: Visibility = Visibility.PUBLIC
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
    day: int | None = None
    night: int | None = None


class TargetAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_seat: int | None = Field(strict=True)
