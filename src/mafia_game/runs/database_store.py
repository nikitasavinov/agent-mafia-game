"""The `GameStore` the engine writes through during a live game.

Adapts an in-progress run to the database, appending one transaction per
committed batch of events so an interrupted game stays readable.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mafia_game.gameplay import GameEvent, GameStatus, Team

from .models import GameTransaction, RunMetadata
from .ports import TransactionWriter


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class DatabaseEventStore:
    def __init__(
        self,
        database: TransactionWriter,
        metadata: RunMetadata,
        run_dir: Path,
        sequence: int = 0,
    ) -> None:
        self.database = database
        self.metadata = metadata
        self.run_dir = run_dir
        self.sequence = sequence
        self.started_clock = time.perf_counter()

    @property
    def game_id(self) -> str:
        return self.metadata.game_id

    def append(self, events: list[GameEvent], transition_id: str | None = None) -> GameTransaction:
        if not events:
            raise ValueError("cannot append an empty transaction")
        sequence = self.sequence + 1
        transaction = GameTransaction(
            game_id=self.metadata.game_id,
            sequence=sequence,
            transition_id=transition_id or uuid4().hex,
            created_at=utc_now(),
            events=events,
        )
        self.database.append_transaction(transaction)
        self.sequence = sequence
        return transaction

    def finish(self, status: GameStatus, winner: Team | None = None) -> None:
        self.metadata.status = status
        self.metadata.winner = winner
        self.metadata.updated_at = utc_now()
        self.database.finish_run(
            self.metadata,
            duration_seconds=time.perf_counter() - self.started_clock,
        )
