"""Operator-only tenant provisioning; no root credential is given to API/worker.

Run with MYSQL_ADMIN_URL and TENANT_ADMIN_PASSWORD supplied via environment.
Passwords are never command arguments or printed. Existing tenants are refused.
"""
import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from services.tenancy import SLUG, configurations, tenant_scope


def provision(slug: str, username: str, password: str):
    if not SLUG.fullmatch(slug) or slug == "default":
        raise ValueError("Invalid tenant slug")
    registry = configurations()
    if slug in registry:
        raise ValueError("Tenant already exists; no credentials or data changed")
    if len(password) < 12:
        raise ValueError("Use at least 12 characters for the tenant administrator password")
    admin_url = make_url(os.environ["MYSQL_ADMIN_URL"])
    # Generated identifiers contain only letters and digits, never user SQL.
    suffix = hashlib.sha256(slug.encode()).hexdigest()[:16]
    database, db_user = "qa_t_" + suffix, "qa_" + suffix
    db_password = secrets.token_hex(32)
    admin_engine = create_engine(admin_url)
    with admin_engine.connect() as connection:
        if connection.scalar(text("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=:name"), {"name": database}):
            raise ValueError("Database already exists; operator must recover incomplete provisioning")
        connection.execute(text(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
        connection.execute(text(f"CREATE USER '{db_user}'@'%' IDENTIFIED BY '{db_password}'"))
        connection.execute(text(f"GRANT ALL PRIVILEGES ON `{database}`.* TO '{db_user}'@'%'") )
    admin_engine.dispose()
    salt = secrets.token_bytes(32)
    registry[slug] = {
        "database_url": admin_url.set(username=db_user, password=db_password, database=database).render_as_string(hide_password=False),
        "username": username, "password_salt": salt.hex(),
        "password_hash": hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 210_000).hex(),
    }
    path = Path(os.getenv("TENANTS_FILE", ".local/runtime/tenants.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    # An operator runs this serially. Atomic replacement prevents partial reads.
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(registry, handle, indent=2)
    temporary.replace(path)
    from db.session import Base, current_engine
    from db import models, catalog_models, auth_models, job_models  # noqa: F401
    from scripts.seed_data import seed
    with tenant_scope(slug):
        Base.metadata.create_all(current_engine())
        seed()
    print(json.dumps({"event": "tenant_provisioned", "tenant": slug, "username": username}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug")
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    password = os.getenv("TENANT_ADMIN_PASSWORD") or getpass.getpass("Tenant admin password: ")
    provision(args.slug, args.username, password)


if __name__ == "__main__":
    main()
