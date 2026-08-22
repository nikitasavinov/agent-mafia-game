from __future__ import annotations

import json
import re
from collections.abc import Callable

import httpx
import pytest

from mafia_game.agents.providers.openai_compatible import OpenAICompatibleDriver
from mafia_game.config import (
    AppConfig,
    GameOptions,
    PlayerConfig,
    Provider,
)
from mafia_game.gameplay import GameRunner, GameStatus, Team
from mafia_game.gameplay.visibility import ReplayView, visible_for_replay
from mafia_game.runs import RunDatabase


@pytest.mark.asyncio
@pytest.mark.parametrize(("seed", "winner"), [(3, Team.TOWN), (19, Team.MAFIA)])
async def test_fake_agents_play_complete_replayable_game(
    config_factory: Callable[..., AppConfig],
    run_database: RunDatabase,
    seed: int,
    winner: Team,
) -> None:
    config = config_factory(seed=seed)
    store = run_database.create_run(f"e2e-{seed}", config, seed)
    runner = GameRunner.create(config, store, seed)

    state = await runner.run()

    assert state.status is GameStatus.COMPLETED
    assert state.winner is winner
    transactions = run_database.read_transactions(store.game_id)
    events = [event for transaction in transactions for event in transaction.events]
    assert events[0].type == "game_started"
    assert events[-1].type == "game_ended"
    assert any(event.type == "day_votes_revealed" for event in events)
    if winner is Team.MAFIA:
        assert any(event.type == "night_actions_locked" for event in events)
    public_messages = [
        event.message for event in events if visible_for_replay(event, ReplayView.PUBLIC)
    ]
    assert not any(message.startswith("Mafia team:") for message in public_messages)
    assert run_database.load_metadata(store.game_id).status is GameStatus.COMPLETED


@pytest.mark.asyncio
async def test_phase_transition_ids_use_the_day_and_night_that_just_started(
    config_factory: Callable[..., AppConfig],
    run_database: RunDatabase,
) -> None:
    config = config_factory(seed=19)
    store = run_database.create_run("transition-labels", config, seed=19)
    runner = GameRunner.create(config, store, seed=19)

    await runner.run()

    numbered_starts = [
        transaction
        for transaction in run_database.read_transactions(store.game_id)
        if re.fullmatch(r"(?:day|night)-\d+-start", transaction.transition_id)
    ]
    assert numbered_starts[0].transition_id == "night-2-start"
    assert numbered_starts[1].transition_id == "day-2-start"
    for transaction in numbered_starts:
        event = transaction.events[0]
        counter = event.day if transaction.transition_id.startswith("day-") else event.night
        period = "day" if transaction.transition_id.startswith("day-") else "night"
        assert transaction.transition_id == f"{period}-{counter}-start"


@pytest.mark.asyncio
async def test_configured_discussion_round_count_is_used(
    config_factory: Callable[..., AppConfig],
    run_database: RunDatabase,
) -> None:
    config = config_factory(seed=3, discussion_rounds=1)
    store = run_database.create_run("one-discussion-round", config, seed=3)
    runner = GameRunner.create(config, store, seed=3)

    await runner.run()

    events = [
        event
        for transaction in run_database.read_transactions(store.game_id)
        for event in transaction.events
    ]
    laps_by_day: dict[int, list[int]] = {}
    for event in events:
        if event.type == "discussion_lap_started" and event.day is not None:
            laps_by_day.setdefault(event.day, []).append(event.data["lap"])
            assert event.data["total_laps"] == 1
    assert laps_by_day
    assert all(laps == [1] for laps in laps_by_day.values())


@pytest.mark.asyncio
async def test_openai_compatible_players_complete_an_end_to_end_game(
    run_database: RunDatabase,
) -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        prompt = payload["messages"][-1]["content"]
        legal_match = re.search(r"Legal target seats: (\[[^\]]*\])", prompt)
        if legal_match:
            legal_targets = json.loads(legal_match.group(1))
            target = legal_targets[0] if legal_targets else None
            answer = json.dumps({"target_seat": target})
        else:
            answer = "I will compare every claim against the public voting record."
        return httpx.Response(
            200,
            json={
                "id": "mock-completion",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": answer},
                    }
                ],
            },
        )

    config = AppConfig(
        game=GameOptions(seed=3, turn_timeout_seconds=1),
        players=[
            PlayerConfig(
                name=f"HTTP {seat}",
                provider=Provider.OPENAI_COMPATIBLE,
                model="mock-model",
                base_url="https://models.example/v1",
            )
            for seat in range(1, 7)
        ],
    )
    store = run_database.create_run("http-e2e", config, seed=3)
    driver = OpenAICompatibleDriver(transport=httpx.MockTransport(handle))
    runner = GameRunner.create(
        config,
        store,
        seed=3,
        drivers={Provider.OPENAI_COMPATIBLE: driver},
    )

    state = await runner.run()

    assert state.status is GameStatus.COMPLETED
    assert state.winner is not None
