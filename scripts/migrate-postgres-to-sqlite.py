from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

from sqlalchemy import MetaData, create_engine, select, text

from spectarr.migrations import upgrade_database


def migrate(source_url: str, destination: Path) -> dict[str, int]:
    if destination.exists():
        raise FileExistsError(f"Refusing to replace existing destination: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination_url = f"sqlite:///{destination}"
    upgrade_database(destination_url)

    source_engine = create_engine(source_url)
    destination_engine = create_engine(destination_url)
    source_metadata = MetaData()
    destination_metadata = MetaData()
    source_metadata.reflect(bind=source_engine)
    destination_metadata.reflect(bind=destination_engine)
    counts: dict[str, int] = {}

    try:
        with source_engine.connect() as source:
            source.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            with destination_engine.connect() as target:
                target.exec_driver_sql("PRAGMA foreign_keys=OFF")
                target.commit()
                with target.begin():
                    for table_name in sorted(destination_metadata.tables):
                        if table_name == "alembic_version":
                            continue
                        if table_name not in source_metadata.tables:
                            counts[table_name] = 0
                            continue
                        source_table = source_metadata.tables[table_name]
                        target_table = destination_metadata.tables[table_name]
                        rows = [dict(row) for row in source.execute(select(source_table)).mappings()]
                        if rows:
                            target.execute(target_table.insert(), rows)
                        copied = target.execute(
                            select(text("count(*)")).select_from(target_table)
                        ).scalar_one()
                        if copied != len(rows):
                            raise RuntimeError(
                                f"Row count mismatch for {table_name}: {len(rows)} source, {copied} copied"
                            )
                        counts[table_name] = copied
                target.exec_driver_sql("PRAGMA foreign_keys=ON")
                target.commit()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        source_engine.dispose()
        destination_engine.dispose()

    with sqlite3.connect(destination) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if integrity != ("ok",):
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"SQLite integrity check failed: {integrity}")
    if foreign_keys:
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"SQLite foreign key check failed: {foreign_keys[:5]}")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate Spectarr metadata from PostgreSQL to SQLite")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--source-url", default=os.getenv("SPECTARR_DATABASE_URL"))
    args = parser.parse_args()
    if not args.source_url:
        parser.error("Provide --source-url or SPECTARR_DATABASE_URL")
    counts = migrate(args.source_url, args.destination)
    for table_name, count in counts.items():
        print(f"{table_name}: {count}")
    print(f"Migrated {sum(counts.values())} rows into {args.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
