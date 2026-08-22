"""The run database: the single source of truth for recorded games.

Wraps SQLite with WAL journaling and foreign keys enabled, and exposes runs,
replay transactions, full-text search, and statistics behind one object.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from mafia_game.config import AppConfig
from mafia_game.gameplay import GameEvent, GameStatus

from . import projections, queries
from .database_store import DatabaseEventStore, utc_now
from .migrations import upgrade_database
from .models import GameTransaction, RunMetadata, RunRecord, SearchResult, StatisticsRow


class RunAlreadyExistsError(ValueError):
    pass


class RunDatabase:
    """SQLite source of truth for runs, replay events, search, and statistics."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace_root = path.parent / ".mafia-workspaces" / path.stem
        upgrade_database(path)
        self.connection = sqlite3.connect(path, timeout=5)
        try:
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA busy_timeout = 5000")
            self.connection.execute("PRAGMA synchronous = NORMAL")
        except BaseException:
            self.connection.close()
            raise

    def __enter__(self) -> RunDatabase:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self.connection.close()

    def create_run(
        self,
        game_id: str,
        config: AppConfig,
        seed: int,
    ) -> DatabaseEventStore:
        now = utc_now()
        metadata = RunMetadata(
            game_id=game_id,
            created_at=now,
            updated_at=now,
            seed=seed,
            status=GameStatus.ACTIVE,
            config=config,
        )
        self.insert_run(metadata)
        return DatabaseEventStore(
            self,
            metadata,
            self.workspace_root / game_id,
        )

    def insert_run(self, metadata: RunMetadata) -> None:
        try:
            with self.connection:
                self.connection.execute(
                    """
                    INSERT INTO runs (
                        game_id, schema_version, created_at, updated_at, seed, status,
                        winner, config_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        metadata.game_id,
                        metadata.schema_version,
                        metadata.created_at,
                        metadata.updated_at,
                        metadata.seed,
                        metadata.status.value,
                        metadata.winner.value if metadata.winner is not None else None,
                        metadata.config.model_dump_json(exclude_none=True),
                    ),
                )
                self.connection.executemany(
                    """
                    INSERT INTO run_players (
                        run_id, seat, name, provider, model, reasoning_effort
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            metadata.game_id,
                            seat,
                            player.name,
                            player.provider.value,
                            player.model,
                            (
                                player.reasoning_effort.value
                                if player.reasoning_effort is not None
                                else None
                            ),
                        )
                        for seat, player in enumerate(metadata.config.players, start=1)
                    ],
                )
        except sqlite3.IntegrityError as error:
            if self.run_exists(metadata.game_id):
                raise RunAlreadyExistsError(f"run already exists: {metadata.game_id}") from error
            raise

    def run_exists(self, game_id: str) -> bool:
        row = self.connection.execute("SELECT 1 FROM runs WHERE game_id = ?", (game_id,)).fetchone()
        return row is not None

    def load_metadata(self, game_id: str) -> RunMetadata:
        row = self.connection.execute(
            """
            SELECT game_id, schema_version, created_at, updated_at, seed, status,
                   winner, config_json
            FROM runs WHERE game_id = ?
            """,
            (game_id,),
        ).fetchone()
        if row is None:
            raise FileNotFoundError(f"run not found: {game_id}")
        return RunMetadata.model_validate(
            {
                "schema_version": row["schema_version"],
                "game_id": row["game_id"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "seed": row["seed"],
                "status": row["status"],
                "winner": row["winner"],
                "config": json.loads(row["config_json"]),
            }
        )

    def read_transactions(self, game_id: str) -> list[GameTransaction]:
        run = self.connection.execute(
            "SELECT last_sequence FROM runs WHERE game_id = ?", (game_id,)
        ).fetchone()
        if run is None:
            raise FileNotFoundError(f"run not found: {game_id}")
        transaction_rows = self.connection.execute(
            """
            SELECT sequence, transition_id, created_at, schema_version
            FROM transactions WHERE run_id = ? ORDER BY sequence
            """,
            (game_id,),
        ).fetchall()
        # One query for every event in the run, grouped in memory, rather than one
        # query per transaction: a long game has hundreds of transactions.
        events_by_sequence: defaultdict[int, list[GameEvent]] = defaultdict(list)
        for event in self.connection.execute(
            """
            SELECT transaction_sequence, type, visibility, message, data_json, day, night
            FROM events
            WHERE run_id = ?
            ORDER BY transaction_sequence, ordinal
            """,
            (game_id,),
        ):
            events_by_sequence[event["transaction_sequence"]].append(
                GameEvent(
                    type=event["type"],
                    visibility=event["visibility"],
                    message=event["message"],
                    data=json.loads(event["data_json"]),
                    day=event["day"],
                    night=event["night"],
                )
            )
        transactions = [
            GameTransaction(
                schema_version=row["schema_version"],
                game_id=game_id,
                sequence=row["sequence"],
                transition_id=row["transition_id"],
                created_at=row["created_at"],
                events=events_by_sequence[row["sequence"]],
            )
            for row in transaction_rows
        ]
        expected = list(range(1, int(run["last_sequence"]) + 1))
        actual = [transaction.sequence for transaction in transactions]
        if actual != expected:
            raise ValueError(f"non-contiguous event sequence: expected {expected}, got {actual}")
        return transactions

    def append_transaction(self, transaction: GameTransaction) -> None:
        projections.append_transaction(self.connection, transaction)

    def finish_run(
        self,
        metadata: RunMetadata,
        *,
        duration_seconds: float | None,
        updated_at: str | None = None,
    ) -> None:
        projections.finish_run(
            self.connection,
            metadata,
            duration_seconds=duration_seconds,
            updated_at=updated_at,
        )

    def list_runs(
        self,
        *,
        status: str | None = None,
        winner: str | None = None,
        model: str | None = None,
        player: str | None = None,
        since: str | None = None,
        limit: int = 50,
    ) -> list[RunRecord]:
        return queries.list_runs(
            self.connection,
            status=status,
            winner=winner,
            model=model,
            player=player,
            since=since,
            limit=limit,
        )

    def search(
        self,
        query: str,
        *,
        include_private: bool = False,
        limit: int = 50,
    ) -> list[SearchResult]:
        return queries.search_runs(
            self.connection,
            query,
            include_private=include_private,
            limit=limit,
        )

    def statistics(
        self,
        group_by: str,
        *,
        since: str | None = None,
        model: str | None = None,
        player: str | None = None,
    ) -> list[StatisticsRow]:
        return queries.statistics(
            self.connection,
            group_by,
            since=since,
            model=model,
            player=player,
        )
