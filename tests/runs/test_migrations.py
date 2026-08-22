from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

import mafia_game.runs.migrations as migrations
from mafia_game.runs import RunDatabase
from mafia_game.runs.migrations import HEAD_REVISION, MigrationError, upgrade_database


def test_fresh_database_is_upgraded_to_alembic_head(tmp_path: Path) -> None:
    path = tmp_path / "fresh.db"
    upgrade_database(path)

    with sqlite3.connect(path) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        legacy_version = connection.execute("PRAGMA user_version").fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        objects = {
            (row[0], row[1])
            for row in connection.execute(
                """
                SELECT type, name FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%' AND name NOT LIKE 'event_search_%'
                """
            )
        }
        run_columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}

    assert revision == HEAD_REVISION
    assert legacy_version == 0
    assert journal_mode == "wal"
    assert objects >= {
        ("table", "alembic_version"),
        ("table", "events"),
        ("table", "event_search"),
        ("table", "run_players"),
        ("table", "runs"),
        ("table", "transactions"),
        ("trigger", "events_search_delete"),
        ("trigger", "events_search_insert"),
    }
    assert {"mode", "simulation_id", "events_retained"}.isdisjoint(run_columns)


def test_repeated_upgrade_is_idempotent_and_preserves_data(tmp_path: Path) -> None:
    path = tmp_path / "upgrade.db"
    upgrade_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE migration_probe (value TEXT NOT NULL)")
        connection.execute("INSERT INTO migration_probe VALUES ('kept')")
        connection.commit()

    upgrade_database(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM migration_probe").fetchall() == [("kept",)]
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            HEAD_REVISION
        )


def test_metadata_has_no_pending_schema_operations(tmp_path: Path) -> None:
    path = tmp_path / "schema-check.db"
    upgrade_database(path)
    engine = sa.create_engine(sa.URL.create("sqlite+pysqlite", database=str(path)))
    config = Config()
    config.set_main_option("script_location", str(migrations.MIGRATIONS_ROOT))
    try:
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.check(config)
    finally:
        engine.dispose()


def test_unknown_database_revision_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "future.db"
    upgrade_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE alembic_version SET version_num = 'future_revision'")
        connection.commit()

    with pytest.raises(MigrationError, match="could not upgrade database"):
        upgrade_database(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            "future_revision"
        )


def test_failed_revision_rolls_back_schema_and_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "broken-migrations"
    versions = root / "versions"
    versions.mkdir(parents=True)
    (root / "env.py").write_text(
        """
from alembic import context

connection = context.config.attributes["connection"]
context.configure(connection=connection, transactional_ddl=True)
with context.begin_transaction():
    context.run_migrations()
""".lstrip(),
        encoding="utf-8",
    )
    (versions / "0001_broken.py").write_text(
        """
import sqlalchemy as sa
from alembic import op

revision = "0001_broken"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("unfinished", sa.Column("id", sa.Integer(), primary_key=True))
    raise RuntimeError("simulated migration failure")

def downgrade():
    pass
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(migrations, "MIGRATIONS_ROOT", root)
    path = tmp_path / "rollback.db"

    with pytest.raises(MigrationError) as caught:
        upgrade_database(path)

    assert str(caught.value.__cause__) == "simulated migration failure"
    with sqlite3.connect(path) as connection:
        assert not _object_exists(connection, "unfinished")
        assert not _object_exists(connection, "alembic_version")


def test_concurrent_openers_both_reach_alembic_head(tmp_path: Path) -> None:
    path = tmp_path / "race.db"
    ready = Barrier(2)

    def open_and_read_revision() -> str:
        ready.wait(timeout=10)
        with RunDatabase(path) as database:
            row = database.connection.execute("SELECT version_num FROM alembic_version").fetchone()
            assert row is not None
            return str(row[0])

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(open_and_read_revision) for _ in range(2)]
        revisions = [future.result(timeout=30) for future in futures]

    assert revisions == [HEAD_REVISION, HEAD_REVISION]


def _object_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name = ?",
        (name,),
    ).fetchone()
    return row is not None
