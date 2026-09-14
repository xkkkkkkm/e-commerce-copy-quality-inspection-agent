"""Small, idempotent bootstrap migrations for the demo's existing MySQL volume.

The project started with ``create_all`` only.  This module keeps upgrades safe for
that existing installation while a full migration tool is still being introduced.
It never drops or rewrites user rows.
"""
from sqlalchemy import inspect, text

from db.session import engine


MIGRATIONS = {
    "inspection_job_items": {
        "lease_owner": "VARCHAR(128) NULL",
        "lease_until": "DATETIME NULL",
        "next_attempt_at": "DATETIME NULL",
        "dispatched_at": "DATETIME NULL",
    },
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


def run_migrations(bind=None) -> list[str]:
    applied: list[str] = []
    with (bind or engine).begin() as connection:
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
        if "inspection_job_items.lease_until" in applied:
            # Preserve active old workers' leases during the schema upgrade.
            connection.execute(text("UPDATE inspection_job_items i JOIN inspection_jobs j ON i.job_id=j.id "
                                    "SET i.lease_owner=j.lease_owner, i.lease_until=j.lease_until WHERE i.status='running'"))
    return applied
