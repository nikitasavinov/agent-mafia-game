"""The contract every player driver implements.

A driver receives a fully rendered prompt and returns text; it never sees the
`GameState` and cannot reach the rules engine. Recoverable failures are values —
a timeout or refusal comes back as a `PlayerResult`, not an exception — so one
flaky provider can degrade a single turn. The gameplay layer escalates permanent
or systemic failures instead of converting an outage into an endless game.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from mafia_game.config import PlayerConfig


class RequestKind(StrEnum):
    SPEECH = "speech"
    DEFENSE = "defense"
    MAFIA_DISCUSSION = "mafia_discussion"
    DAY_VOTE = "day_vote"
    RUNOFF_VOTE = "runoff_vote"
    MAFIA_VOTE = "mafia_vote"
    INVESTIGATE = "investigate"
    PROTECT = "protect"
    REPAIR = "repair"
    PROBE = "probe"


class ResultStatus(StrEnum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    REFUSAL = "refusal"
    TRANSPORT_ERROR = "transport_error"
    FATAL_ERROR = "fatal_error"


class PlayerRequest(BaseModel):
    turn_id: str
    seat: int
    kind: RequestKind
    prompt: str
    timeout_seconds: float
    word_limit: int | None = None
    legal_targets: list[int] = Field(default_factory=list)
    allow_none: bool = True


class PlayerResult(BaseModel):
    status: ResultStatus
    text: str = ""
    raw: str = ""
    provider_request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is ResultStatus.SUCCESS


class DriverCheck(BaseModel):
    name: str
    ok: bool
    detail: str


class DriverSession(Protocol):
    async def respond(self, request: PlayerRequest) -> PlayerResult: ...

    async def close(self) -> None: ...


class PlayerDriver(Protocol):
    async def preflight(
        self, player: PlayerConfig, workspace: Path, *, live: bool = False
    ) -> list[DriverCheck]: ...

    async def open(self, player: PlayerConfig, workspace: Path) -> DriverSession: ...
