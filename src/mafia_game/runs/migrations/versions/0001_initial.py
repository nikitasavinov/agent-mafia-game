"""Create run history, event search, and statistics projections.

Revision ID: 0001_initial
Revises: None
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("game_id", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("winner", sa.Text(), nullable=True),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("day", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("night", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("event_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_sequence", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.PrimaryKeyConstraint("game_id", name="pk_runs"),
    )
    op.create_table(
        "run_players",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("seat", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("reasoning_effort", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=True),
        sa.Column("team", sa.Text(), nullable=True),
        sa.Column("won", sa.Integer(), nullable=True),
        sa.Column("requests", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failures", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "failure_statuses_json",
            sa.Text(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("usage_json", sa.Text(), server_default=sa.text("'{}'"), nullable=False),
        sa.ForeignKeyConstraint(
            ("run_id",),
            ("runs.game_id",),
            name="fk_run_players_run_id_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "seat", name="pk_run_players"),
    )
    op.create_table(
        "transactions",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("transition_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ("run_id",),
            ("runs.game_id",),
            name="fk_transactions_run_id_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "sequence", name="pk_transactions"),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("transaction_sequence", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("visibility", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data_json", sa.Text(), nullable=False),
        sa.Column("day", sa.Integer(), nullable=True),
        sa.Column("night", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ("run_id", "transaction_sequence"),
            ("transactions.run_id", "transactions.sequence"),
            name="fk_events_run_id_transactions",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_events"),
        sa.UniqueConstraint(
            "run_id",
            "transaction_sequence",
            "ordinal",
            name="uq_events_run_id",
        ),
    )

    op.create_index("runs_created_at_idx", "runs", [sa.text("created_at DESC")])
    op.create_index("runs_status_winner_idx", "runs", ("status", "winner"))
    op.create_index(
        "run_players_model_idx",
        "run_players",
        ("provider", "model", "reasoning_effort"),
    )
    op.create_index("run_players_name_idx", "run_players", ("name",))
    op.create_index("events_run_type_idx", "events", ("run_id", "type"))

    op.execute(
        """
        CREATE VIRTUAL TABLE event_search USING fts5(
            message,
            run_id UNINDEXED,
            event_id UNINDEXED,
            tokenize = 'unicode61'
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER events_search_insert
        AFTER INSERT ON events BEGIN
            INSERT INTO event_search(rowid, message, run_id, event_id)
            VALUES (new.id, new.message, new.run_id, new.id);
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER events_search_delete
        AFTER DELETE ON events BEGIN
            DELETE FROM event_search WHERE rowid = old.id;
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS events_search_delete")
    op.execute("DROP TRIGGER IF EXISTS events_search_insert")
    op.execute("DROP TABLE IF EXISTS event_search")
    op.drop_table("events")
    op.drop_table("transactions")
    op.drop_table("run_players")
    op.drop_table("runs")
