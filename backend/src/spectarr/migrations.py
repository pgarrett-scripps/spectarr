from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.schema import CreateColumn

from .config import get_settings
from .database import Base
from . import models  # noqa: F401


def run_migrations() -> None:
    upgrade_database(get_settings().database_url)


def upgrade_database(database_url: str) -> None:
    backend_root = migration_root()
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    migration_engine = create_engine(database_url)
    table_names = set(inspect(migration_engine).get_table_names())
    if "projects" in table_names and "alembic_version" not in table_names:
        adopt_legacy_database(migration_engine)
        command.stamp(config, "head")
        migration_engine.dispose()
        return
    migration_engine.dispose()
    command.upgrade(config, "head")


def migration_root() -> Path:
    configured = os.getenv("SPECTARR_MIGRATION_ROOT")
    candidates = [Path(configured)] if configured else []
    candidates.extend([Path.cwd(), Path(__file__).resolve().parents[2]])
    for candidate in candidates:
        if (candidate / "alembic.ini").is_file() and (candidate / "alembic").is_dir():
            return candidate
    raise RuntimeError("Could not locate Spectarr Alembic migrations")


def adopt_legacy_database(migration_engine) -> None:
    """Reconcile the pre-Alembic MVP schema without replacing existing tables."""
    with migration_engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            inspector = inspect(connection)
            existing_tables = set(inspector.get_table_names())
            if table.name not in existing_tables:
                table.create(connection)
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
            preparer = connection.dialect.identifier_preparer
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                definition = str(CreateColumn(column).compile(dialect=connection.dialect))
                connection.exec_driver_sql(
                    f"ALTER TABLE {preparer.quote(table.name)} ADD COLUMN {definition}"
                )
        for table in Base.metadata.sorted_tables:
            for index in table.indexes:
                index.create(connection, checkfirst=True)
