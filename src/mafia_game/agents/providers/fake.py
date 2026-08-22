"""Deterministic in-process driver.

Answers every request instantly and legally, which is what makes the end-to-end
and architecture tests fast and free of network access.
"""

from __future__ import annotations

from pathlib import Path

from mafia_game.config import PlayerConfig

from ..contracts import (
    DriverCheck,
    DriverSession,
    PlayerRequest,
    PlayerResult,
    RequestKind,
    ResultStatus,
)


class FakeSession:
    def __init__(self, name: str) -> None:
        self.name = name
        self.turn = 0

    async def respond(self, request: PlayerRequest) -> PlayerResult:
        self.turn += 1
        if request.kind in {
            RequestKind.DAY_VOTE,
            RequestKind.RUNOFF_VOTE,
            RequestKind.MAFIA_VOTE,
            RequestKind.INVESTIGATE,
            RequestKind.PROTECT,
            RequestKind.REPAIR,
        }:
            target = request.legal_targets[0] if request.legal_targets else None
            text = f'{{"target_seat": {target if target is not None else "null"}}}'
        elif request.kind is RequestKind.PROBE:
            text = "READY"
        else:
            text = (
                f"I am {self.name}. On turn {self.turn}, I want the table to compare votes "
                "carefully and hold contradictions accountable."
            )
        return PlayerResult(status=ResultStatus.SUCCESS, text=text, raw=text)

    async def close(self) -> None:
        return None


class FakeDriver:
    async def preflight(
        self, player: PlayerConfig, workspace: Path, *, live: bool = False
    ) -> list[DriverCheck]:
        del player, workspace, live
        return [DriverCheck(name="fake", ok=True, detail="deterministic in-process driver")]

    async def open(self, player: PlayerConfig, workspace: Path) -> DriverSession:
        del workspace
        return FakeSession(player.name)
