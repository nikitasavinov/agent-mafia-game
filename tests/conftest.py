from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from mafia_game.config import AppConfig, GameOptions, PlayerConfig, Provider
from mafia_game.runs import RunDatabase


@pytest.fixture
def config_factory() -> Callable[..., AppConfig]:
    def make_config(
        count: int = 6,
        *,
        provider: Provider = Provider.FAKE,
        seed: int = 42,
        discussion_rounds: int = 3,
    ) -> AppConfig:
        return AppConfig(
            game=GameOptions(
                seed=seed,
                max_concurrency=4,
                turn_timeout_seconds=1,
                discussion_rounds=discussion_rounds,
                speech_word_limit=40,
                defense_word_limit=30,
                private_planning_word_limit=30,
            ),
            players=[
                PlayerConfig(name=f"Player {seat}", provider=provider, model="test-model")
                for seat in range(1, count + 1)
            ],
        )

    return make_config


@pytest.fixture
def run_database(tmp_path: Path) -> Iterator[RunDatabase]:
    with RunDatabase(tmp_path / "mafia.db") as database:
        yield database
