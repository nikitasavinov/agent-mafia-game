"""Alembic environment for the embedded SQLite run database."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Literal, TypeAlias, cast

import sqlalchemy as sa
from alembic import context
from sqlalchemy.engine import Connection

from mafia_game.runs.schema import FTS_OBJECT_PREFIX, METADATA

config = context.config
target_metadata = METADATA

ObjectKind: TypeAlias = Literal[
    "schema",
    "table",
    "column",
    "index",
    "unique_constraint",
    "foreign_key_constraint",
    "check_constraint",
]
ParentName: TypeAlias = Literal["schema_name", "table_name", "schema_qualified_table_name"]


def _include_name(
    name: str | None,
    type_: ObjectKind,
    parent_names: MutableMapping[ParentName, str | None],
) -> bool:
    del parent_names
    return not (type_ == "table" and name is not None and name.startswith(FTS_OBJECT_PREFIX))


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_name=_include_name,
        render_as_batch=True,
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url") or "sqlite+pysqlite:///mafia.db"
    context.configure(
        url=url,
        target_metadata=target_metadata,
        compare_type=True,
        include_name=_include_name,
        literal_binds=True,
        render_as_batch=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied = cast(Connection | None, config.attributes.get("connection"))
    if supplied is not None:
        _configure(supplied)
        return

    arguments = context.get_x_argument(as_dictionary=True)
    path = arguments.get("database", "mafia.db")
    engine = sa.create_engine(sa.URL.create("sqlite+pysqlite", database=path))
    try:
        with engine.connect() as connection:
            _configure(connection)
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
