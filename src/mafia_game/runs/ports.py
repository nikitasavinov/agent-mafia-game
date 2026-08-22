"""The run-database write surface used by ``DatabaseEventStore``."""

from __future__ import annotations

from typing import Protocol

from .models import GameTransaction, RunMetadata


class TransactionWriter(Protocol):
    """Appending to, and closing, a run that already exists."""

    def append_transaction(self, transaction: GameTransaction) -> None: ...

    def finish_run(
        self,
        metadata: RunMetadata,
        *,
        duration_seconds: float | None,
        updated_at: str | None = None,
    ) -> None: ...
