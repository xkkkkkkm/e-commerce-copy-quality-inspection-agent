"""Small synchronous Elasticsearch HTTP adapter, also used by the indexer."""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import httpx
from dotenv import load_dotenv


def bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    value = float(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须在 {minimum} 和 {maximum} 之间")
    return value


@dataclass(frozen=True)
class ElasticsearchSettings:
    url: str
    index: str
    timeout: float
    headers: dict[str, str]
    auth: tuple[str, str] | None

    @classmethod
    def from_env(cls, index: str | None = None) -> "ElasticsearchSettings":
        load_dotenv()
        index = index or os.getenv("ELASTICSEARCH_INDEX", "quality_rules")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", index):
            raise ValueError("ELASTICSEARCH_INDEX 格式无效")
        headers = {}
        api_key = os.getenv("ELASTICSEARCH_API_KEY", "").strip()
        if api_key:
            headers["Authorization"] = f"ApiKey {api_key}"
        username = os.getenv("ELASTICSEARCH_USERNAME", "")
        password = os.getenv("ELASTICSEARCH_PASSWORD", "")
        auth = (username, password) if username and not api_key else None
        return cls(
            os.getenv("ELASTICSEARCH_URL", "http://127.0.0.1:9200").rstrip("/"),
            index,
            bounded_float("ELASTICSEARCH_TIMEOUT_SECONDS", 2.0, 0.1, 30.0),
            headers,
            auth,
        )


@contextmanager
def managed_client(client: httpx.Client | None, timeout: float) -> Iterator[httpx.Client]:
    if client is not None:
        yield client
    else:
        with httpx.Client(timeout=timeout, follow_redirects=False) as owned:
            yield owned


INDEX_PROPERTIES = {
    "rule_id": {"type": "keyword"},
    "category": {"type": "keyword"},
    "issue_type": {"type": "keyword"},
    "risk_level": {"type": "keyword"},
    "rule_text": {"type": "text"},
    "bad_examples": {"type": "text"},
    "rewrite_hint": {"type": "text"},
    "version": {"type": "keyword"},
    "status": {"type": "keyword"},
    "source_version": {"type": "keyword"},
}

INDEX_MAPPING = {"dynamic": "strict", "properties": INDEX_PROPERTIES}
