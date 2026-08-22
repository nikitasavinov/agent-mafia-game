"""Turning committed events into queryable rows.

The event log stays the source of truth; everything here is a derived
projection — per-player request counts, token usage, roles, abort reasons —
maintained inside the same transaction that appends the events.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter

from mafia_game.gameplay import GameEvent, GameStatus
from mafia_game.gameplay.event_payloads import optional_int

from .models import GameTransaction, RunMetadata
from .usage import add_numeric_totals


def append_transaction(
    connection: sqlite3.Connection,
    transaction: GameTransaction,
) -> None:
    with connection:
        cursor = connection.execute(
            """
            UPDATE runs SET last_sequence = ?, event_count = event_count + ?
            WHERE game_id = ? AND last_sequence = ?
            """,
            (
                transaction.sequence,
                len(transaction.events),
                transaction.game_id,
                transaction.sequence - 1,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"non-contiguous transaction sequence for run {transaction.game_id}")
        connection.execute(
            """
            INSERT INTO transactions (
                run_id, sequence, transition_id, created_at, schema_version
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                transaction.game_id,
                transaction.sequence,
                transaction.transition_id,
                transaction.created_at,
                transaction.schema_version,
            ),
        )
        for ordinal, event in enumerate(transaction.events):
            event_payload = event.model_dump(mode="json")
            connection.execute(
                """
                INSERT INTO events (
                    run_id, transaction_sequence, ordinal, type, visibility,
                    message, data_json, day, night
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction.game_id,
                    transaction.sequence,
                    ordinal,
                    event.type,
                    str(event.visibility),
                    event.message,
                    json.dumps(
                        event_payload["data"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    event.day,
                    event.night,
                ),
            )
        apply_event_projections(connection, transaction.game_id, transaction.events)


def apply_event_projections(
    connection: sqlite3.Connection,
    game_id: str,
    events: list[GameEvent],
) -> None:
    days = [event.day for event in events if event.day is not None]
    nights = [event.night for event in events if event.night is not None]
    if days or nights:
        connection.execute(
            """
            UPDATE runs
            SET day = MAX(day, ?), night = MAX(night, ?)
            WHERE game_id = ?
            """,
            (max(days, default=0), max(nights, default=1), game_id),
        )
    for event in events:
        seat = optional_int(event.data.get("seat"))
        if event.type == "role_assigned" and seat is not None:
            role = event.data.get("role")
            team = event.data.get("team")
            connection.execute(
                """
                UPDATE run_players SET role = ?, team = ?
                WHERE run_id = ? AND seat = ?
                """,
                (
                    role if isinstance(role, str) else None,
                    team if isinstance(team, str) else None,
                    game_id,
                    seat,
                ),
            )
        elif event.type in {"agent_response", "agent_failure"} and seat is not None:
            _apply_player_result(connection, game_id, seat, event)
        elif event.type == "game_aborted":
            reason = event.data.get("reason")
            connection.execute(
                "UPDATE runs SET error = ? WHERE game_id = ?",
                (reason if isinstance(reason, str) else event.message, game_id),
            )


def finish_run(
    connection: sqlite3.Connection,
    metadata: RunMetadata,
    *,
    duration_seconds: float | None,
    updated_at: str | None = None,
) -> None:
    winner = metadata.winner.value if metadata.winner is not None else None
    finished_at = updated_at or metadata.updated_at
    with connection:
        connection.execute(
            """
            UPDATE runs
            SET updated_at = ?, status = ?, winner = ?, duration_seconds = ?
            WHERE game_id = ?
            """,
            (
                finished_at,
                metadata.status.value,
                winner,
                duration_seconds,
                metadata.game_id,
            ),
        )
        if metadata.status is GameStatus.COMPLETED and winner is not None:
            connection.execute(
                """
                UPDATE run_players
                SET won = CASE WHEN team = ? THEN 1 ELSE 0 END
                WHERE run_id = ?
                """,
                (winner, metadata.game_id),
            )


def _apply_player_result(
    connection: sqlite3.Connection,
    game_id: str,
    seat: int,
    event: GameEvent,
) -> None:
    row = connection.execute(
        """
        SELECT failure_statuses_json, usage_json
        FROM run_players WHERE run_id = ? AND seat = ?
        """,
        (game_id, seat),
    ).fetchone()
    if row is None:
        return
    failure_statuses = Counter(json.loads(row["failure_statuses_json"]))
    usage: dict[str, float] = json.loads(row["usage_json"])
    failed = event.type == "agent_failure"
    status = event.data.get("status")
    if failed and isinstance(status, str):
        failure_statuses[status] += 1
    add_numeric_totals(usage, event.data.get("usage"))
    connection.execute(
        """
        UPDATE run_players
        SET requests = requests + 1,
            failures = failures + ?,
            failure_statuses_json = ?,
            usage_json = ?
        WHERE run_id = ? AND seat = ?
        """,
        (
            int(failed),
            json.dumps(dict(failure_statuses), separators=(",", ":"), sort_keys=True),
            json.dumps(usage, separators=(",", ":"), sort_keys=True),
            game_id,
            seat,
        ),
    )
