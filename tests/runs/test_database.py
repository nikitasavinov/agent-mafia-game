from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from mafia_game.config import AppConfig, PlayerConfig, Provider
from mafia_game.gameplay import (
    GameEvent,
    GameStatus,
    Role,
    Team,
    Visibility,
)
from mafia_game.runs import RunDatabase
from mafia_game.runs.migrations import HEAD_REVISION


def populated_database_run(database: RunDatabase, config: AppConfig, game_id: str) -> None:
    store = database.create_run(game_id, config, seed=77)
    role_events = []
    for seat in range(1, 7):
        role = Role.MAFIA if seat == 1 else Role.CIVILIAN
        team = Team.MAFIA if role is Role.MAFIA else Team.TOWN
        role_events.append(
            GameEvent(
                type="role_assigned",
                visibility=Visibility.player(seat),
                message=f"role {seat}",
                data={"seat": seat, "role": role.value, "team": team.value},
                night=1,
            )
        )
    store.append(role_events, "setup")
    store.append(
        [
            GameEvent(
                type="agent_response",
                visibility=Visibility.INTERNAL,
                message="private needle",
                data={
                    "seat": 2,
                    "status": "success",
                    "usage": {"total_tokens": 123},
                },
                day=1,
            )
        ],
        "response",
    )
    store.append(
        [
            GameEvent(
                type="speech",
                message="#2 Player 2: public needle in a haystack",
                data={"seat": 2, "text": "public needle in a haystack"},
                day=1,
            )
        ],
        "speech",
    )
    store.finish(GameStatus.COMPLETED, Team.TOWN)


def test_database_at_alembic_head_reopens_without_migration(
    tmp_path: Path, config_factory: Callable[..., AppConfig]
) -> None:
    path = tmp_path / "mafia.db"
    with RunDatabase(path) as database:
        populated_database_run(database, config_factory(), "existing-run")

    with sqlite3.connect(path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert revision == HEAD_REVISION

    with RunDatabase(path) as reopened:
        assert reopened.load_metadata("existing-run").status is GameStatus.COMPLETED
        assert reopened.read_transactions("existing-run")


def test_database_round_trip_search_filters_and_statistics(
    tmp_path: Path, config_factory: Callable[..., AppConfig]
) -> None:
    path = tmp_path / "mafia.db"
    with RunDatabase(path) as database:
        populated_database_run(database, config_factory(), "game-one")

        metadata = database.load_metadata("game-one")
        transactions = database.read_transactions("game-one")
        records = database.list_runs(winner="town", model="test-model")
        public_matches = database.search("public needle")
        hidden_matches = database.search("private needle")
        all_matches = database.search("private needle", include_private=True)
        statistics = database.statistics("model")
        faction_statistics = database.statistics("faction")

    assert metadata.status is GameStatus.COMPLETED
    assert metadata.winner is Team.TOWN
    assert [transaction.sequence for transaction in transactions] == [1, 2, 3]
    assert len(records) == 1
    assert records[0].event_count == 8
    assert [match.game_id for match in public_matches] == ["game-one"]
    assert hidden_matches == []
    assert [match.visibility for match in all_matches] == [Visibility.INTERNAL]
    assert len(statistics) == 1
    assert statistics[0].appearances == 6
    assert statistics[0].wins == 5
    assert statistics[0].requests == 1
    assert statistics[0].usage["total_tokens"] == 123
    assert [(row.label, row.wins, row.appearances) for row in faction_statistics] == [
        ("town", 1, 1),
        ("mafia", 0, 1),
    ]


def test_database_masks_inline_api_keys(tmp_path: Path) -> None:
    secret = "database-secret"
    config = AppConfig(
        players=[
            PlayerConfig(
                name="HTTP",
                provider=Provider.OPENAI_COMPATIBLE,
                model="remote-model",
                base_url="https://models.example/v1",
                api_key=secret,
            ),
            *[
                PlayerConfig(name=f"P{seat}", provider=Provider.FAKE, model="fake")
                for seat in range(2, 7)
            ],
        ]
    )

    with RunDatabase(tmp_path / "mafia.db") as database:
        database.create_run("secret-game", config, seed=1)
        stored = database.connection.execute(
            "SELECT config_json FROM runs WHERE game_id = 'secret-game'"
        ).fetchone()[0]

    assert secret not in stored
    assert '"api_key":"**********"' in stored
