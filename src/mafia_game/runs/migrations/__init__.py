"""Programmatic Alembic upgrades for the embedded run database."""

from __future__ import annotations

from pathlib import Path
from time import monotonic, sleep
from typing import Final

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection

HEAD_REVISION: Final = "0001_initial"
MIGRATIONS_ROOT = Path(__file__).parent
LOCK_TIMEOUT_SECONDS: Final = 5.0
LOCK_RETRY_SECONDS: Final = 0.05


class MigrationError(ValueError):
    """Raised when Alembic cannot upgrade a run database safely."""


def upgrade_database(path: Path) -> None:
    """Upgrade ``path`` to Alembic head under a SQLite write lock.

    ``BEGIN IMMEDIATE`` serializes concurrent application openers before either
    one reads the current revision. Alembic then runs on that same connection,
    keeping schema changes and the revision marker in one transaction.
    """

    engine = sa.create_engine(
        sa.URL.create("sqlite+pysqlite", database=str(path)),
        connect_args={"timeout": 5},
    )
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys = ON")
            connection.exec_driver_sql("PRAGMA busy_timeout = 5000")
            _enable_wal(connection, path)
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                command.upgrade(_config(connection), "head")
                _check_integrity(connection)
            except BaseException as error:
                if connection.in_transaction():
                    connection.rollback()
                if isinstance(error, MigrationError):
                    raise
                if isinstance(error, Exception):
                    raise MigrationError(f"could not upgrade database {path}") from error
                raise
            else:
                connection.commit()
    finally:
        engine.dispose()


def _enable_wal(connection: Connection, path: Path) -> None:
    """Enable persistent WAL mode, retrying SQLite's non-busy-aware PRAGMA."""
    deadline = monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            current = str(connection.exec_driver_sql("PRAGMA journal_mode").scalar_one())
            if current.casefold() == "wal":
                return
            selected = str(connection.exec_driver_sql("PRAGMA journal_mode = WAL").scalar_one())
            if selected.casefold() == "wal":
                return
        except sa.exc.OperationalError as error:
            if "locked" not in str(error.orig).casefold():
                raise MigrationError(f"could not enable WAL mode for database {path}") from error
        if monotonic() >= deadline:
            raise MigrationError(f"timed out enabling WAL mode for database {path}")
        sleep(LOCK_RETRY_SECONDS)


def _config(connection: Connection | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_ROOT))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def _check_integrity(connection: Connection) -> None:
    foreign_key_violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    if foreign_key_violations:
        raise RuntimeError(
            f"migration introduced {len(foreign_key_violations)} foreign-key violation(s)"
        )
    result = connection.exec_driver_sql("PRAGMA quick_check").scalar_one_or_none()
    if result != "ok":
        raise RuntimeError(f"SQLite integrity check failed: {result or 'no result'}")


__all__ = ["HEAD_REVISION", "MigrationError", "upgrade_database"]
