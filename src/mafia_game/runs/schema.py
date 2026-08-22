"""SQLAlchemy Core schema used to author and verify Alembic migrations.

Runtime queries deliberately stay on the standard-library ``sqlite3`` module.
This metadata is the desired schema Alembic compares against when generating or
checking revisions; it does not introduce ORM models into the application.
"""

from __future__ import annotations

import sqlalchemy as sa

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

METADATA = sa.MetaData(naming_convention=NAMING_CONVENTION)

RUNS = sa.Table(
    "runs",
    METADATA,
    sa.Column("game_id", sa.Text, primary_key=True),
    sa.Column("schema_version", sa.Integer, nullable=False),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.Column("seed", sa.Integer, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("winner", sa.Text),
    sa.Column("config_json", sa.Text, nullable=False),
    sa.Column("duration_seconds", sa.Float),
    sa.Column("day", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("night", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("error", sa.Text),
    sa.Column("event_count", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("last_sequence", sa.Integer, nullable=False, server_default=sa.text("0")),
)

RUN_PLAYERS = sa.Table(
    "run_players",
    METADATA,
    sa.Column(
        "run_id",
        sa.Text,
        sa.ForeignKey("runs.game_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("seat", sa.Integer, primary_key=True),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("model", sa.Text, nullable=False),
    sa.Column("reasoning_effort", sa.Text),
    sa.Column("role", sa.Text),
    sa.Column("team", sa.Text),
    sa.Column("won", sa.Integer),
    sa.Column("requests", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("failures", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column(
        "failure_statuses_json",
        sa.Text,
        nullable=False,
        server_default=sa.text("'{}'"),
    ),
    sa.Column("usage_json", sa.Text, nullable=False, server_default=sa.text("'{}'")),
)

TRANSACTIONS = sa.Table(
    "transactions",
    METADATA,
    sa.Column(
        "run_id",
        sa.Text,
        sa.ForeignKey("runs.game_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("sequence", sa.Integer, primary_key=True),
    sa.Column("transition_id", sa.Text, nullable=False),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("schema_version", sa.Integer, nullable=False),
)

EVENTS = sa.Table(
    "events",
    METADATA,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("run_id", sa.Text, nullable=False),
    sa.Column("transaction_sequence", sa.Integer, nullable=False),
    sa.Column("ordinal", sa.Integer, nullable=False),
    sa.Column("type", sa.Text, nullable=False),
    sa.Column("visibility", sa.Text, nullable=False),
    sa.Column("message", sa.Text, nullable=False),
    sa.Column("data_json", sa.Text, nullable=False),
    sa.Column("day", sa.Integer),
    sa.Column("night", sa.Integer),
    sa.ForeignKeyConstraint(
        ("run_id", "transaction_sequence"),
        ("transactions.run_id", "transactions.sequence"),
        ondelete="CASCADE",
    ),
    sa.UniqueConstraint("run_id", "transaction_sequence", "ordinal"),
)

sa.Index("runs_created_at_idx", RUNS.c.created_at.desc())
sa.Index("runs_status_winner_idx", RUNS.c.status, RUNS.c.winner)
sa.Index(
    "run_players_model_idx",
    RUN_PLAYERS.c.provider,
    RUN_PLAYERS.c.model,
    RUN_PLAYERS.c.reasoning_effort,
)
sa.Index("run_players_name_idx", RUN_PLAYERS.c.name)
sa.Index("events_run_type_idx", EVENTS.c.run_id, EVENTS.c.type)

FTS_OBJECT_PREFIX = "event_search"

__all__ = ["FTS_OBJECT_PREFIX", "METADATA"]
