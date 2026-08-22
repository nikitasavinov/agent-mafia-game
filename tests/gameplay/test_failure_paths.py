"""Engine behaviour when a provider misbehaves.

The per-provider tests cover a driver reporting a failure. These cover what the
engine does with it: cancelling sibling turns, recording a usable abort reason,
replacing a dead session, and never losing events a seat has not been shown.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from mafia_game.agents import (
    DriverCheck,
    DriverSession,
    PlayerRequest,
    PlayerResult,
    RequestKind,
    ResultStatus,
)
from mafia_game.agents.providers.openai_compatible import OpenAICompatibleDriver
from mafia_game.config import AppConfig, PlayerConfig, Provider
from mafia_game.gameplay import (
    AgentFailureLimitError,
    GameEvent,
    GameRunner,
    GameStalledError,
    GameStatus,
    ProviderRejectedError,
    Visibility,
)
from mafia_game.gameplay.engine import describe_error
from mafia_game.gameplay.observer import NullObserver
from mafia_game.gameplay.turns import TurnManager
from mafia_game.runs import RunDatabase

ACTION_KINDS = {
    RequestKind.DAY_VOTE,
    RequestKind.RUNOFF_VOTE,
    RequestKind.MAFIA_VOTE,
    RequestKind.INVESTIGATE,
    RequestKind.PROTECT,
    RequestKind.REPAIR,
}

Behaviour = Callable[[PlayerRequest], object]


def legal_answer(request: PlayerRequest) -> str:
    """A response the engine will always accept, whatever the request kind."""
    if request.kind in ACTION_KINDS:
        return json.dumps(
            {"target_seat": request.legal_targets[0] if request.legal_targets else None}
        )
    return "I have nothing new to add."


class ScriptedSession:
    """A session whose behaviour per request is supplied by the test.

    Returning an exception raises it; returning a `PlayerResult` yields it; any
    other value is treated as successful response text.
    """

    def __init__(self, behaviour: Behaviour) -> None:
        self.behaviour = behaviour
        self.prompts: list[str] = []
        self.closed = False

    async def respond(self, request: PlayerRequest) -> PlayerResult:
        self.prompts.append(request.prompt)
        outcome = self.behaviour(request)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, PlayerResult):
            return outcome
        if asyncio.iscoroutine(outcome):
            outcome = await outcome
        text = str(outcome)
        return PlayerResult(status=ResultStatus.SUCCESS, text=text, raw=text)

    async def close(self) -> None:
        self.closed = True


class ScriptedDriver:
    def __init__(self, behaviour: Behaviour) -> None:
        self.behaviour = behaviour
        self.sessions: list[ScriptedSession] = []

    async def preflight(
        self, player: PlayerConfig, workspace: Path, *, live: bool = False
    ) -> list[DriverCheck]:
        del player, workspace, live
        return [DriverCheck(name="scripted", ok=True, detail="test double")]

    async def open(self, player: PlayerConfig, workspace: Path) -> DriverSession:
        del player, workspace
        session = ScriptedSession(self.behaviour)
        self.sessions.append(session)
        return session


@pytest.mark.asyncio
async def test_insufficient_credit_aborts_after_the_first_request(
    run_database: RunDatabase,
) -> None:
    calls = 0

    async def handle(raw_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del raw_request
        calls += 1
        return httpx.Response(
            402,
            json={"error": {"message": "Insufficient credits"}},
        )

    config = AppConfig(
        game={"seed": 19, "discussion_rounds": 1, "turn_timeout_seconds": 1},
        players=[
            PlayerConfig(
                name=f"HTTP {seat}",
                provider=Provider.OPENAI_COMPATIBLE,
                model="remote-model",
                base_url="https://models.example/v1",
            )
            for seat in range(1, 7)
        ],
    )
    store = run_database.create_run("insufficient-credit", config, 19)
    runner = GameRunner.create(
        config,
        store,
        19,
        drivers={
            Provider.OPENAI_COMPATIBLE: OpenAICompatibleDriver(
                transport=httpx.MockTransport(handle)
            )
        },
    )

    with pytest.raises(ProviderRejectedError, match="HTTP 402: Insufficient credits"):
        await runner.run()

    assert calls == 1
    assert runner.state.status is GameStatus.ABORTED
    assert sum(event.type == "agent_failure" for event in runner.events) == 1
    assert not any(event.day == 2 for event in runner.events)
    aborted = [event for event in runner.events if event.type == "game_aborted"]
    assert len(aborted) == 1
    assert "permanently rejected" in aborted[0].message


@pytest.mark.asyncio
async def test_full_table_of_transient_failures_trips_circuit_breaker(
    config_factory: Callable[..., AppConfig],
    run_database: RunDatabase,
) -> None:
    failure = PlayerResult(
        status=ResultStatus.TRANSPORT_ERROR,
        detail="upstream unavailable",
    )
    config = config_factory(seed=19, discussion_rounds=1)
    store = run_database.create_run("systemic-failure", config, 19)
    runner = GameRunner.create(
        config,
        store,
        19,
        drivers={Provider.FAKE: ScriptedDriver(lambda _request: failure)},
    )

    with pytest.raises(AgentFailureLimitError, match="6 consecutive model requests failed"):
        await runner.run()

    assert runner.state.status is GameStatus.ABORTED
    assert sum(event.type == "agent_failure" for event in runner.events) == 6
    assert not any(event.type == "day_votes_revealed" for event in runner.events)


@pytest.mark.asyncio
async def test_one_transient_failure_does_not_abort_the_game(
    config_factory: Callable[..., AppConfig],
    run_database: RunDatabase,
) -> None:
    calls = 0

    def behaviour(request: PlayerRequest) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            return PlayerResult(
                status=ResultStatus.TRANSPORT_ERROR,
                detail="one dropped connection",
            )
        return legal_answer(request)

    config = config_factory(seed=19, discussion_rounds=1)
    store = run_database.create_run("isolated-failure", config, 19)
    runner = GameRunner.create(
        config,
        store,
        19,
        drivers={Provider.FAKE: ScriptedDriver(behaviour)},
    )

    state = await runner.run()

    assert state.status is GameStatus.COMPLETED
    assert sum(event.type == "agent_failure" for event in runner.events) == 1


@pytest.mark.asyncio
async def test_repeated_no_action_days_abort_as_a_stalemate(
    config_factory: Callable[..., AppConfig],
    run_database: RunDatabase,
) -> None:
    def behaviour(request: PlayerRequest) -> str:
        if request.kind in ACTION_KINDS:
            return json.dumps({"target_seat": None})
        return ""

    config = config_factory(seed=19, discussion_rounds=1)
    config.game.max_stalemate_days = 2
    store = run_database.create_run("stalemate", config, 19)
    runner = GameRunner.create(
        config,
        store,
        19,
        drivers={Provider.FAKE: ScriptedDriver(behaviour)},
    )

    with pytest.raises(GameStalledError, match="2 consecutive days"):
        await runner.run()

    assert runner.state.status is GameStatus.ABORTED
    assert runner.state.day == 2
    assert runner.stalemate_days == 2
    assert not any(event.day == 3 for event in runner.events)


@pytest.mark.asyncio
async def test_a_failing_night_turn_cancels_its_siblings(
    config_factory: Callable[..., AppConfig],
    run_database: RunDatabase,
) -> None:
    """A Mafia turn that raises must not leave the Detective and Doctor running.

    Orphaned turns hold provider sessions that `close_all` is tearing down, and
    can still commit events after the game has already aborted.
    """
    started: list[RequestKind] = []

    async def stall(kind: RequestKind) -> str:
        started.append(kind)
        await asyncio.sleep(30)
        return "too late"

    def behaviour(request: PlayerRequest) -> object:
        if request.kind is RequestKind.MAFIA_VOTE:
            return RuntimeError("provider exploded")
        if request.kind in {RequestKind.INVESTIGATE, RequestKind.PROTECT}:
            return stall(request.kind)
        return legal_answer(request)

    config = config_factory(seed=19, discussion_rounds=1)
    store = run_database.create_run("night-failure", config, 19)
    runner = GameRunner.create(
        config, store, 19, drivers={Provider.FAKE: ScriptedDriver(behaviour)}
    )

    before = asyncio.all_tasks()
    # TaskGroup reports the failing turn as a group; describe_error unwraps it.
    with pytest.raises(BaseExceptionGroup) as caught:
        await runner.run()

    assert started, "expected the concurrent night turns to have started"
    leaked = {task for task in asyncio.all_tasks() if task not in before and not task.done()}
    assert leaked == set(), f"night turns still running after the phase failed: {leaked}"
    assert "provider exploded" in describe_error(caught.value)
    assert runner.state.status is GameStatus.ABORTED


@pytest.mark.asyncio
async def test_a_failing_day_ballot_cancels_its_siblings(
    config_factory: Callable[..., AppConfig],
    run_database: RunDatabase,
) -> None:
    """Day voting must contain a failing turn exactly as Night actions do.

    Every living seat votes at once, so the same rule applies: one seat raising
    cannot leave the others running against sessions that are being closed.
    """
    started: list[int] = []

    async def stall(seat: int) -> str:
        started.append(seat)
        await asyncio.sleep(30)
        return "too late"

    def behaviour(request: PlayerRequest) -> object:
        if request.kind is not RequestKind.DAY_VOTE:
            return legal_answer(request)
        if request.seat == 1:
            return RuntimeError("provider exploded")
        return stall(request.seat)

    config = config_factory(seed=19, discussion_rounds=1)
    store = run_database.create_run("day-failure", config, 19)
    runner = GameRunner.create(
        config, store, 19, drivers={Provider.FAKE: ScriptedDriver(behaviour)}
    )

    before = asyncio.all_tasks()
    with pytest.raises(BaseExceptionGroup) as caught:
        await runner.run()

    assert started, "expected the concurrent day ballots to have started"
    leaked = {task for task in asyncio.all_tasks() if task not in before and not task.done()}
    assert leaked == set(), f"day ballots still running after the phase failed: {leaked}"
    assert "provider exploded" in describe_error(caught.value)
    assert runner.state.status is GameStatus.ABORTED


@pytest.mark.asyncio
async def test_abort_reason_names_the_underlying_driver_error(
    config_factory: Callable[..., AppConfig],
    run_database: RunDatabase,
) -> None:
    """The recorded reason must survive TaskGroup's ExceptionGroup wrapper."""

    def behaviour(request: PlayerRequest) -> object:
        if request.kind is RequestKind.MAFIA_VOTE:
            return RuntimeError("upstream 503")
        return legal_answer(request)

    config = config_factory(seed=19, discussion_rounds=1)
    store = run_database.create_run("abort-reason", config, 19)
    runner = GameRunner.create(
        config, store, 19, drivers={Provider.FAKE: ScriptedDriver(behaviour)}
    )
    with pytest.raises(BaseExceptionGroup):
        await runner.run()

    aborted = [event for event in runner.events if event.type == "game_aborted"]
    assert len(aborted) == 1
    assert "RuntimeError: upstream 503" in aborted[0].message
    assert runner.state.status is GameStatus.ABORTED


def _turn_manager(
    tmp_path: Path,
    config: AppConfig,
    behaviour: Behaviour,
    events: list[GameEvent],
) -> tuple[TurnManager, ScriptedDriver]:
    from mafia_game.gameplay.rules import create_game

    state, _ = create_game("g", config, config.game.seed or 1)
    driver = ScriptedDriver(behaviour)

    def commit(new: list[GameEvent], transition_id: str | None = None) -> None:
        del transition_id
        events.extend(new)

    manager = TurnManager(
        config,
        state,
        events,
        tmp_path,
        NullObserver(),
        commit,
        {Provider.FAKE: driver},
    )
    return manager, driver


@pytest.mark.asyncio
async def test_a_replacement_session_is_told_the_history_exactly_once(
    tmp_path: Path,
    config_factory: Callable[..., AppConfig],
) -> None:
    """A discarded session rewinds the cursor; opening its replacement must not rewind again.

    Rewinding twice resends the entire history to a session that has just
    received it, wasting tokens and showing the player every event twice.
    """
    calls = {"n": 0}

    def behaviour(request: PlayerRequest) -> object:
        calls["n"] += 1
        if calls["n"] == 1:
            return PlayerResult(status=ResultStatus.TIMEOUT, detail="session died")
        return legal_answer(request)

    config = config_factory(seed=1)
    events = [
        GameEvent(type="speech", message=f"EVENT-{i}", visibility=Visibility.PUBLIC)
        for i in range(3)
    ]
    manager, driver = _turn_manager(tmp_path, config, behaviour, events)
    await manager.open_all()
    for _ in range(3):
        await manager.get_speech(1, RequestKind.SPEECH, 40)
    await manager.close_all()

    seat_one_prompts = [
        prompt for session in driver.sessions for prompt in session.prompts if "#1" in prompt
    ]
    carried = [
        [marker for marker in ("EVENT-0", "EVENT-1", "EVENT-2") if marker in prompt]
        for prompt in seat_one_prompts
    ]
    assert carried[0] == ["EVENT-0", "EVENT-1", "EVENT-2"], "first turn carries the backlog"
    assert carried[1] == ["EVENT-0", "EVENT-1", "EVENT-2"], "replacement session is re-told it"
    assert carried[2] == [], "the same session must not be told a third time"


@pytest.mark.asyncio
async def test_a_repair_turn_does_not_consume_unseen_history(
    tmp_path: Path,
    config_factory: Callable[..., AppConfig],
) -> None:
    """A repair prompt restates the failed action, not the history.

    If it advanced the cursor, every event committed since the seat's last real
    turn would be silently dropped for that player.
    """

    def behaviour(request: PlayerRequest) -> object:
        if request.kind is RequestKind.DAY_VOTE:
            return "not json at all"
        return legal_answer(request)

    config = config_factory(seed=3)
    events = [
        GameEvent(type="speech", message="#2 spoke", visibility=Visibility.PUBLIC),
        GameEvent(type="speech", message="#3 spoke", visibility=Visibility.PUBLIC),
    ]
    manager, _ = _turn_manager(tmp_path, config, behaviour, events)
    await manager.open_all()

    target = await manager.get_action(1, RequestKind.DAY_VOTE, [2, 3], allow_none=True)
    await manager.close_all()

    assert target == 2, "the repair attempt should have produced a legal action"
    # The real turn consumed exactly the two seeded events; the repair consumed none.
    assert manager.players[1].history_cursor == 2
    assert len(events) > 2, "the exchange should have recorded internal events"
