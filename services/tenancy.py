"""Database-per-tenant routing. Only an operator can register database bindings.

The cookie prefix selects a database, never grants access: the random bearer
token must still exist in that database. HTTP callers cannot supply a DSN.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import re

tenant_id = ContextVar("tenant_id", default="default")
SLUG = re.compile(r"[a-z][a-z0-9-]{0,31}\Z")


def configurations() -> dict:
    path = os.getenv("TENANTS_FILE", ".local/runtime/tenants.json")
    if not Path(path).exists():
        return {}
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or any(not SLUG.fullmatch(k) or k == "default" for k in data):
        raise RuntimeError("Invalid tenant registry")
    return data


def tenant_names() -> list[str]:
    return ["default", *sorted(configurations())]


@contextmanager
def tenant_scope(name: str):
    if name != "default" and name not in configurations():
        raise ValueError("Unknown tenant")
    token = tenant_id.set(name)
    try:
        yield
    finally:
        tenant_id.reset(token)


def evaluation_path(default: Path) -> Path:
    # Slugs come only from the validated operator registry.
    return default if tenant_id.get() == "default" else default.parent / tenant_id.get() / default.name
