"""Driving one player's turn, from prompt to validated action.

`TurnManager` owns the provider sessions: it feeds each seat only the events it
may read, bounds concurrency, gives a malformed structured action one repair
attempt, and rebuilds a session that timed out or died. Every exchange is
recorded as an internal event so a finished run can be audited.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from mafia_game.agents import (
    DriverSession,
    PlayerDriver,
    PlayerRequest,
    PlayerResult,
    RequestKind,
    ResultStatus,
    default_drivers,
)
from mafia_game.config import AppConfig, PlayerConfig, Provider

from .errors import AgentFailureLimitError, GameAbortError, ProviderRejectedError
from .models import GameEvent, GameState, TargetAction, Visibility
from .observer import GameObserver
from .ports import CommitEvents
from .prompts import build_prompt, build_repair_prompt, normalize_speech
from .rules import validate_target
from .visibility import event_history_lines


@dataclass(slots=True)
class ManagedPlayer:
    config: PlayerConfig
    driver: PlayerDriver
    workspace: Path
    session: DriverSession | None = None
    history_cursor: int = 0


class TurnManager:
    def __init__(
        self,
        config: AppConfig,
        state: GameState,
        events: list[GameEvent],
        run_dir: Path,
        observer: GameObserver,
        commit: CommitEvents,
        drivers: dict[Provider, PlayerDriver] | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.events = events
        self.observer = observer
        self.commit = commit
        self.semaphore = asyncio.Semaphore(config.game.max_concurrency)
        self.consecutive_model_failures = 0
        self.failure_circuit_open = False
        registry = drivers or default_drivers()
        self.players = {
            seat: ManagedPlayer(
                config=player,
                driver=registry[player.provider],
                workspace=run_dir / "workspaces" / f"seat-{seat}",
            )
            for seat, player in enumerate(config.players, start=1)
        }

    async def open_all(self) -> None:
        for seat in sorted(self.players):
            await self._ensure_session(seat)

    async def close_all(self) -> None:
        sessions = [managed.session for managed in self.players.values() if managed.session]
        if sessions:
            await asyncio.gather(
                *(session.close() for session in sessions),
                return_exceptions=True,
            )
        for managed in self.players.values():
            managed.session = None

    async def get_speech(self, seat: int, kind: RequestKind, word_limit: int) -> str:
        result, _request = await self._ask(
            seat,
            kind,
            legal_targets=[],
            word_limit=word_limit,
            allow_none=True,
        )
        if not result.ok:
            return ""
        return normalize_speech(result.text, word_limit)

    async def get_action(
        self,
        seat: int,
        kind: RequestKind,
        legal_targets: list[int],
        *,
        allow_none: bool,
    ) -> int | None:
        result, request = await self._ask(
            seat,
            kind,
            legal_targets=legal_targets,
            word_limit=None,
            allow_none=allow_none,
        )
        if not result.ok:
            self.observer.on_locked(self.state, seat, kind)
            return None
        target, error = parse_target(result.text, legal_targets, allow_none=allow_none)
        for _attempt in range(self.config.game.repair_attempts):
            if error is None:
                break
            repair_prompt = build_repair_prompt(
                request.prompt, error, legal_targets, allow_none=allow_none
            )
            result, _repair_request = await self._ask(
                seat,
                RequestKind.REPAIR,
                legal_targets=legal_targets,
                word_limit=None,
                allow_none=allow_none,
                prompt_override=repair_prompt,
            )
            if not result.ok:
                self.observer.on_locked(self.state, seat, kind)
                return None
            target, error = parse_target(result.text, legal_targets, allow_none=allow_none)
        if error is not None:
            self.commit(
                [
                    GameEvent(
                        type="invalid_action",
                        visibility=Visibility.INTERNAL,
                        message=f"Seat {seat} action fell back: {error}",
                        data={"seat": seat, "kind": kind.value, "error": error},
                        day=self.state.day or None,
                        night=self.state.night,
                    )
                ]
            )
            self.observer.on_locked(self.state, seat, kind)
            return None
        self.observer.on_locked(self.state, seat, kind)
        return target

    async def _ensure_session(self, seat: int) -> DriverSession:
        """Return this seat's session, opening a replacement if it was discarded.

        This deliberately leaves `history_cursor` alone. Rewinding belongs to
        `_discard_session`, which knows the transcript was lost; doing it here
        too would undo the advance `_ask` has already made and resend the whole
        history to a session that just received it.
        """
        managed = self.players[seat]
        if managed.session is None:
            managed.workspace.mkdir(parents=True, exist_ok=True)
            managed.session = await managed.driver.open(managed.config, managed.workspace)
        return managed.session

    async def _discard_session(self, seat: int) -> None:
        """Drop a session that timed out or died, and rewind its history.

        The replacement starts with no memory of the game, so the next prompt
        must carry the full event history again.
        """
        managed = self.players[seat]
        session = managed.session
        managed.session = None
        managed.history_cursor = 0
        if session is not None:
            with suppress(Exception):
                await session.close()

    async def _ask(
        self,
        seat: int,
        kind: RequestKind,
        *,
        legal_targets: list[int],
        word_limit: int | None,
        allow_none: bool,
        prompt_override: str | None = None,
    ) -> tuple[PlayerResult, PlayerRequest]:
        managed = self.players[seat]
        if prompt_override is not None:
            # A repair prompt restates the failed action instead of the history, so
            # the cursor must not move: whatever this seat has not been shown yet is
            # still owed to it on its next real turn.
            prompt = prompt_override
        else:
            visible = event_history_lines(self.events[managed.history_cursor :], self.state, seat)
            prompt = build_prompt(
                self.state,
                seat,
                kind,
                visible,
                legal_targets,
                word_limit,
                allow_none=allow_none,
                discussion_rounds=self.config.game.discussion_rounds,
            )
            managed.history_cursor = len(self.events)
        request = PlayerRequest(
            turn_id=uuid4().hex,
            seat=seat,
            kind=kind,
            prompt=prompt,
            timeout_seconds=self.config.game.turn_timeout_seconds,
            word_limit=word_limit,
            legal_targets=legal_targets,
            allow_none=allow_none,
        )
        self.commit(
            [
                GameEvent(
                    type="agent_request",
                    visibility=Visibility.INTERNAL,
                    message=f"Request {request.turn_id} sent to seat {seat}.",
                    data=request.model_dump(mode="json"),
                    day=self.state.day or None,
                    night=self.state.night,
                )
            ],
            request.turn_id,
        )
        self.observer.on_thinking(self.state, seat, kind)
        try:
            session = await self._ensure_session(seat)
        except Exception as error:
            result = PlayerResult(
                status=ResultStatus.TRANSPORT_ERROR,
                detail=f"could not restart provider session: {error}",
            )
        else:
            async with self.semaphore:
                result = await session.respond(request)
        self.commit(
            [
                GameEvent(
                    type="agent_response" if result.ok else "agent_failure",
                    visibility=Visibility.INTERNAL,
                    message=(
                        f"Seat {seat} answered {request.turn_id}."
                        if result.ok
                        else f"Seat {seat} failed {request.turn_id}: {result.status.value}."
                    ),
                    data={
                        "turn_id": request.turn_id,
                        "seat": seat,
                        "kind": kind.value,
                        "status": result.status.value,
                        "text": result.text,
                        "raw": result.raw,
                        "detail": result.detail,
                        "provider_request_id": result.provider_request_id,
                        "usage": result.usage,
                    },
                    day=self.state.day or None,
                    night=self.state.night,
                )
            ],
            request.turn_id,
        )
        if result.status in {
            ResultStatus.TIMEOUT,
            ResultStatus.CANCELLED,
            ResultStatus.TRANSPORT_ERROR,
            ResultStatus.FATAL_ERROR,
        }:
            await self._discard_session(seat)
        abort_error = self._register_result(seat, kind, result)
        if abort_error is not None:
            raise abort_error
        return result, request

    def _register_result(
        self,
        seat: int,
        kind: RequestKind,
        result: PlayerResult,
    ) -> GameAbortError | None:
        """Apply the model-failure circuit breaker after recording the result."""
        if self.players[seat].config.provider is Provider.HUMAN:
            return None
        if self.failure_circuit_open:
            return None
        if result.ok:
            self.consecutive_model_failures = 0
            return None

        self.consecutive_model_failures += 1
        detail = " ".join((result.detail or result.status.value).split())[:500]
        failure = f"seat {seat} failed {kind.value} with {result.status.value}: {detail}"
        if result.status is ResultStatus.FATAL_ERROR:
            self.failure_circuit_open = True
            return ProviderRejectedError(f"provider permanently rejected a request; {failure}")

        active_model_seats = sum(
            player.alive and self.players[player.seat].config.provider is not Provider.HUMAN
            for player in self.state.players
        )
        failure_limit = max(1, active_model_seats)
        if self.consecutive_model_failures >= failure_limit:
            self.failure_circuit_open = True
            return AgentFailureLimitError(
                f"{self.consecutive_model_failures} consecutive model requests failed; {failure}"
            )
        return None


def parse_target(
    text: str, legal_targets: list[int], *, allow_none: bool
) -> tuple[int | None, str | None]:
    candidate = text.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[0].strip() in {"```", "```json"}:
            candidate = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as error:
        return None, f"invalid JSON: {error.msg}"
    try:
        action = TargetAction.model_validate(payload)
    except ValidationError as error:
        return None, f"invalid action shape: {error.errors(include_url=False)}"
    if not validate_target(action.target_seat, legal_targets, allow_none=allow_none):
        return None, f"target {action.target_seat!r} is not legal"
    return action.target_seat, None
