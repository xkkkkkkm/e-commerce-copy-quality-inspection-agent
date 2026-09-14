"""Small, idempotent bootstrap migrations for the demo's existing MySQL volume.

The project started with ``create_all`` only.  This module keeps upgrades safe for
that existing installation while a full migration tool is still being introduced.
It never drops or rewrites user rows.
"""
from sqlalchemy import inspect, text

from db.session import engine


MIGRATIONS = {
    "inspection_tasks": {
        "input_json": "JSON NULL",
        "input_hash": "VARCHAR(64) NULL",
        "idempotency_key": "VARCHAR(128) NULL",
    },
    # Queue tables were introduced after the first demo volumes.  Existing
    # queue rows receive an empty legacy hash and remain queryable; new rows
    # always write the canonical payload hash from services.jobs.
    "inspection_jobs": {
        "payload_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
    },
}


def run_migrations() -> list[str]:
    applied: list[str] = []
    with engine.begin() as connection:
        inspector = inspect(connection)
        tables = set(inspector.get_table_names())
        for table, columns in MIGRATIONS.items():
            if table not in tables:
                continue
            existing = {column["name"] for column in inspector.get_columns(table)}
            for column, definition in columns.items():
                if column in existing:
                    continue
                connection.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}"))
                applied.append(f"{table}.{column}")
    return applied
