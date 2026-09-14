"""Synchronise the Elasticsearch rule alias from authoritative MySQL rules."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import httpx

from rag.elasticsearch import ElasticsearchSettings, INDEX_MAPPING, managed_client
from rag.rules import RuleDocument, load_rule_documents


class RuleIndexingError(RuntimeError):
    pass


def load_mysql_rules(session_factory: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
    """Read all MySQL rows, including disabled tombstones."""
    from sqlalchemy import select
    from db.models import QualityRule
    from db.session import SessionLocal

    with (session_factory or SessionLocal)() as session:
        records = session.scalars(select(QualityRule).order_by(QualityRule.rule_id)).all()
        return [RuleDocument.model_validate({name: getattr(record, name)
                for name in RuleDocument.model_fields if hasattr(record, name)}).model_dump(exclude_none=True)
                for record in records]


def _source_version(rules: list[dict[str, Any]]) -> str:
    payload = json.dumps(rules, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def index_rules(
    rules_path: str | Path | None = None, *, http_client: httpx.Client | None = None,
    index: str | None = None, session_factory: Callable[..., Any] | None = None,
) -> int:
    # A path is an explicit fixture import. Normal operation is MySQL-only.
    rules = load_rule_documents(rules_path) if rules_path is not None else load_mysql_rules(session_factory)
    settings = ElasticsearchSettings.from_env(index=index)
    alias = settings.index
    source_version = _source_version(rules)
    concrete = f"{alias}_{source_version}"
    endpoint = f"{settings.url}/{concrete}"
    request_options = {"headers": settings.headers, "auth": settings.auth, "timeout": settings.timeout}
    try:
        with managed_client(http_client, settings.timeout) as client:
            existing = client.head(endpoint, **request_options)
            if existing.status_code == 404:
                created = client.put(
                    endpoint,
                    json={"mappings": {**INDEX_MAPPING, "_meta": {
                        "source": "mysql" if rules_path is None else "explicit_fixture",
                        "source_version": source_version,
                    }}},
                    **request_options,
                )
                created.raise_for_status()
            else:
                existing.raise_for_status()
            if rules:
                lines: list[str] = []
                for rule in rules:
                    lines.append(json.dumps({"index": {"_id": rule["rule_id"]}}, ensure_ascii=False))
                    lines.append(json.dumps({**rule, "source_version": source_version}, ensure_ascii=False))
                bulk = client.post(f"{endpoint}/_bulk", params={"refresh": "wait_for"},
                                   content=("\n".join(lines) + "\n").encode("utf-8"),
                                   headers={**settings.headers, "Content-Type": "application/x-ndjson"},
                                   auth=settings.auth, timeout=settings.timeout)
                bulk.raise_for_status()
                result = bulk.json()
                items = result.get("items", [])
                if result.get("errors") or len(items) != len(rules) or any(
                    not 200 <= item.get("index", {}).get("status", 500) < 300 for item in items):
                    raise RuleIndexingError("Elasticsearch 规则导入存在失败项，请修复后重新导入")
            if rules_path is None:
                alias_probe = client.get(f"{settings.url}/_alias/{alias}", **request_options)
                actions = []
                if alias_probe.status_code == 200:
                    actions.append({"remove": {"index": f"{alias}_*", "alias": alias}})
                elif alias_probe.status_code == 404:
                    # Older installs used the alias name as a concrete index.
                    # Remove that obsolete container only after the new version
                    # has been fully indexed, then create the alias.
                    legacy = client.head(f"{settings.url}/{alias}", **request_options)
                    if legacy.status_code == 200:
                        removed = client.delete(f"{settings.url}/{alias}", **request_options)
                        removed.raise_for_status()
                actions.append({"add": {"index": concrete, "alias": alias}})
                switched = client.post(f"{settings.url}/_aliases", json={"actions": actions}, **request_options)
                switched.raise_for_status()
        return len(rules)
    except RuleIndexingError:
        raise
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise RuleIndexingError("Elasticsearch 规则导入失败，请检查连接、认证和索引映射") from None


def main() -> int:
    parser = argparse.ArgumentParser(description="从 MySQL 规则表创建版本化 ES 索引并切换 alias")
    parser.add_argument("--rules", type=Path, default=None,
                        help="显式使用 JSON fixture 导入（仅用于初始化/离线演示）")
    parser.add_argument("--index", default=None)
    args = parser.parse_args()
    try:
        count = index_rules(args.rules, index=args.index)
    except (RuleIndexingError, ValueError, OSError) as exc:
        print(f"规则导入失败：{exc}")
        return 1
    print(f"规则导入完成：{count} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
