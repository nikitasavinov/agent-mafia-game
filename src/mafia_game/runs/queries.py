"""Read-only queries over recorded runs: filtering, search, and aggregation.

Search runs against an FTS5 index. Private events are excluded unless the caller
explicitly opts in, which is why the visibility predicate is built here rather
than left to callers.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter

from mafia_game.gameplay import Audience, Team, Visibility

from .models import RunRecord, SearchResult, StatisticsRow
from .usage import add_numeric_totals


def list_runs(
    connection: sqlite3.Connection,
    *,
    status: str | None = None,
    winner: str | None = None,
    model: str | None = None,
    player: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> list[RunRecord]:
    clauses: list[str] = []
    parameters: list[object] = []
    if status is not None:
        clauses.append("r.status = ?")
        parameters.append(status)
    if winner is not None:
        clauses.append("r.winner = ?")
        parameters.append(winner)
    if since is not None:
        clauses.append("r.created_at >= ?")
        parameters.append(since)
    if model is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM run_players p WHERE p.run_id = r.game_id "
            "AND p.model LIKE ? COLLATE NOCASE)"
        )
        parameters.append(f"%{model}%")
    if player is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM run_players p WHERE p.run_id = r.game_id "
            "AND p.name LIKE ? COLLATE NOCASE)"
        )
        parameters.append(f"%{player}%")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    parameters.append(limit)
    # Only fixed SQL clauses are composed here; user values remain bound parameters.
    rows = connection.execute(
        """
        SELECT r.*,
               (SELECT COUNT(*) FROM run_players p WHERE p.run_id = r.game_id)
                   AS player_count,
               (SELECT GROUP_CONCAT(label, ' | ') FROM (
                   SELECT DISTINCT p.provider || '/' || p.model ||
                       CASE WHEN p.reasoning_effort IS NULL THEN ''
                            ELSE '/' || p.reasoning_effort END AS label
                   FROM run_players p WHERE p.run_id = r.game_id
                   ORDER BY label
               )) AS models
        FROM runs r
        """
        + where
        + " ORDER BY r.created_at DESC LIMIT ?",
        parameters,
    ).fetchall()
    return [
        RunRecord(
            game_id=row["game_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            status=row["status"],
            winner=row["winner"],
            seed=row["seed"],
            day=row["day"],
            night=row["night"],
            duration_seconds=row["duration_seconds"],
            event_count=row["event_count"],
            player_count=row["player_count"],
            models=tuple((row["models"] or "").split(" | ")) if row["models"] else (),
        )
        for row in rows
    ]


def search_runs(
    connection: sqlite3.Connection,
    query: str,
    *,
    include_private: bool = False,
    limit: int = 50,
) -> list[SearchResult]:
    phrase = '"' + query.strip().replace('"', '""') + '"'
    if phrase == '""':
        raise ValueError("search query must not be blank")
    public_only = f" AND e.visibility = '{Audience.PUBLIC.value}'"
    visibility = "" if include_private else public_only
    # Visibility changes a fixed predicate; the search phrase remains a bound parameter.
    rows = connection.execute(
        """
        SELECT e.run_id, r.created_at, e.type, e.visibility, e.message, e.day, e.night
        FROM event_search f
        JOIN events e ON e.id = f.rowid
        JOIN runs r ON r.game_id = e.run_id
        WHERE event_search MATCH ?
        """
        + visibility
        + " ORDER BY bm25(event_search), r.created_at DESC LIMIT ?",
        (phrase, limit),
    ).fetchall()
    return [
        SearchResult(
            game_id=row["run_id"],
            created_at=row["created_at"],
            event_type=row["type"],
            visibility=Visibility.parse(row["visibility"]),
            message=row["message"],
            day=row["day"],
            night=row["night"],
        )
        for row in rows
    ]


def statistics(
    connection: sqlite3.Connection,
    group_by: str,
    *,
    since: str | None = None,
    model: str | None = None,
    player: str | None = None,
) -> list[StatisticsRow]:
    clauses = ["r.status = 'completed'", "r.winner IS NOT NULL"]
    parameters: list[object] = []
    if since is not None:
        clauses.append("r.created_at >= ?")
        parameters.append(since)
    if model is not None:
        clauses.append("p.model LIKE ? COLLATE NOCASE")
        parameters.append(f"%{model}%")
    if player is not None:
        clauses.append("p.name LIKE ? COLLATE NOCASE")
        parameters.append(f"%{player}%")
    # Only fixed filter clauses are composed here; all filter values are parameterized.
    rows = connection.execute(
        """
        SELECT r.game_id, r.winner, p.name, p.provider, p.model,
               p.reasoning_effort, p.team, p.won, p.requests, p.failures, p.usage_json
        FROM runs r JOIN run_players p ON p.run_id = r.game_id
        WHERE
        """
        + " AND ".join(clauses),
        parameters,
    ).fetchall()
    if group_by == "faction":
        game_winners: dict[str, str] = {}
        for row in rows:
            game_winners[row["game_id"]] = row["winner"]
        total = len(game_winners)
        counts = Counter(game_winners.values())
        return [
            StatisticsRow(label=team.value, appearances=total, wins=counts[team.value])
            for team in Team
        ]
    aggregates: dict[str, StatisticsRow] = {}
    for row in rows:
        if group_by == "provider":
            label = row["provider"]
        elif group_by == "player":
            label = row["name"]
        else:
            effort = row["reasoning_effort"] or "default"
            label = f"{row['provider']}/{row['model']}/{effort}"
        item = aggregates.setdefault(label, StatisticsRow(label=label))
        item.appearances += 1
        item.wins += int(row["won"] or 0)
        if row["team"] == Team.TOWN.value:
            item.town_appearances += 1
            item.town_wins += int(row["won"] or 0)
        elif row["team"] == Team.MAFIA.value:
            item.mafia_appearances += 1
            item.mafia_wins += int(row["won"] or 0)
        item.requests += row["requests"]
        item.failures += row["failures"]
        add_numeric_totals(item.usage, json.loads(row["usage_json"]))
    return sorted(
        aggregates.values(),
        key=lambda item: (-item.win_rate, -item.appearances, item.label.casefold()),
    )
