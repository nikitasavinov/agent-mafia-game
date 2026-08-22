from __future__ import annotations

import json
import re
from io import StringIO
from pathlib import Path

from rich.console import Console
from rich.text import Text

from mafia_game.agents import PlayerRequest, RequestKind, ResultStatus
from mafia_game.agents.providers.fake import FakeDriver
from mafia_game.agents.providers.human import HumanDriver
from mafia_game.config import AppConfig, PlayerConfig, Provider
from mafia_game.gameplay import GameRunner, GameStatus, RichGameObserver
from mafia_game.runs import RunDatabase


def request(
    kind: RequestKind,
    *,
    word_limit: int | None = None,
    legal_targets: list[int] | None = None,
    allow_none: bool = True,
) -> PlayerRequest:
    return PlayerRequest(
        turn_id="turn-1",
        seat=1,
        kind=kind,
        prompt="private game context",
        timeout_seconds=1,
        word_limit=word_limit,
        legal_targets=legal_targets or [],
        allow_none=allow_none,
    )


def output_console() -> tuple[Console, StringIO]:
    stream = StringIO()
    return (
        Console(file=stream, force_terminal=True, color_system="standard", width=90),
        stream,
    )


async def test_human_speech_reprompts_when_over_word_limit(tmp_path: Path) -> None:
    console, stream = output_console()
    answers = iter(["one two three four", "I suspect Ada"])
    driver = HumanDriver(
        console,
        input_function=lambda _prompt: next(answers),
        interactive=True,
    )
    session = await driver.open(PlayerConfig(name="You", provider=Provider.HUMAN), tmp_path)

    result = await session.respond(request(RequestKind.SPEECH, word_limit=3))

    assert result.status is ResultStatus.SUCCESS
    assert result.text == "I suspect Ada"
    assert "shorten it to 3 or fewer" in Text.from_ansi(stream.getvalue()).plain


async def test_human_action_accepts_names_and_serializes_exact_json(tmp_path: Path) -> None:
    console, stream = output_console()
    answers = iter(["unknown", "Babbage"])
    driver = HumanDriver(
        console,
        player_names={2: "Babbage", 3: "Curie"},
        input_function=lambda _prompt: next(answers),
        interactive=True,
    )
    session = await driver.open(PlayerConfig(name="You", provider=Provider.HUMAN), tmp_path)

    result = await session.respond(
        request(
            RequestKind.DAY_VOTE,
            legal_targets=[2, 3],
            allow_none=True,
        )
    )

    assert result.ok
    assert json.loads(result.text) == {"target_seat": 2}
    assert result.provider_request_id == "human:turn-1"
    assert "Choose one of: #2, #3, or #0" in Text.from_ansi(stream.getvalue()).plain


async def test_human_driver_preflight_requires_interactive_input(tmp_path: Path) -> None:
    driver = HumanDriver(interactive=False)

    checks = await driver.preflight(
        PlayerConfig(name="You", provider=Provider.HUMAN),
        tmp_path,
    )

    assert len(checks) == 1
    assert not checks[0].ok
    assert "not an interactive terminal" in checks[0].detail


async def test_runner_completes_with_one_scripted_human_player(
    run_database: RunDatabase,
) -> None:
    config = AppConfig(
        game={
            "seed": 17,
            "discussion_rounds": 1,
            "turn_timeout_seconds": 1,
            "speech_word_limit": 30,
            "defense_word_limit": 20,
            "private_planning_word_limit": 20,
        },
        players=[
            PlayerConfig(name="You", provider=Provider.HUMAN),
            *[
                PlayerConfig(name=f"P{seat}", provider=Provider.FAKE, model="fake")
                for seat in range(2, 7)
            ],
        ],
    )
    console, _stream = output_console()
    prompts: list[str] = []

    def scripted_input(prompt: str) -> str:
        prompts.append(prompt)
        if "You ›" in prompt:
            return "PASS"
        match = re.search(r"#(\d+)", prompt)
        assert match is not None
        return match.group(1)

    names = {seat: player.name for seat, player in enumerate(config.players, start=1)}
    human_driver = HumanDriver(
        console,
        player_names=names,
        input_function=scripted_input,
        interactive=True,
    )
    store = run_database.create_run("human-game", config, seed=17)
    observer = RichGameObserver(console, perspective_seat=1)
    runner = GameRunner.create(
        config,
        store,
        17,
        observer=observer,
        drivers={Provider.HUMAN: human_driver, Provider.FAKE: FakeDriver()},
    )
    observer.show_roster(runner.state)

    state = await runner.run()

    assert state.status is GameStatus.COMPLETED
    assert prompts
    assert store.metadata.status is GameStatus.COMPLETED
    assert not any(
        event.type == "agent_failure" and event.data.get("seat") == 1 for event in runner.events
    )
