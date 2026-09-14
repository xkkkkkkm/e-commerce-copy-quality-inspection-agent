"""Retrieve enabled rule evidence from Elasticsearch, MySQL, or a local fixture."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import httpx

from rag.elasticsearch import ElasticsearchSettings, managed_client
from rag.rules import (
    DEFAULT_RULES_PATH, RuleDocument, applicable_rules, load_rule_documents, rank_rules,
)


class RuleRetrievalError(RuntimeError):
    pass


class RuleRetriever:
    """Auto: ES -> MySQL -> JSON on failure; local mode never accesses the network.

    A successful empty response is authoritative, so disabled or deleted database
    rules cannot be re-enabled by silently replacing an empty result with JSON.
    ``last_source`` and ``last_warning`` describe the most recent operation.
    """

    def __init__(
        self, backend: str = "auto", *, http_client: httpx.Client | None = None,
        session_factory: Callable[..., Any] | None = None,
        rules_path: str | Path = DEFAULT_RULES_PATH,
    ):
        if backend not in {"auto", "local", "mysql", "elasticsearch", "es"}:
            raise ValueError("不支持的规则检索后端")
        self.backend = backend
        from services.tenancy import tenant_id
        if tenant_id.get() != "default" and backend != "local":
            # Tenant policy is private; the shared ES index contains only the
            # default platform policy and must never override tenant rules.
            self.backend = "mysql"
        self.http_client = http_client
        self.session_factory = session_factory
        self.rules_path = Path(rules_path)
        self.last_source = ""
        self.last_warning = ""
        self.last_version = ""

    @staticmethod
    def _validate_arguments(category: str, top_k: int | None = None) -> None:
        if category not in {"食品", "美妆", "3C", "通用"}:
            raise ValueError("不支持的商品类目")
        if top_k is not None and (type(top_k) is not int or not 1 <= top_k <= 100):
            raise ValueError("top_k 必须为 1 到 100 的整数")

    def retrieve(
        self, category: str, query: str, top_k: int = 5, issue_type: str | None = None,
    ) -> list[dict[str, Any]]:
        self._validate_arguments(category, top_k)
        if not isinstance(query, str):
            raise ValueError("query 必须是字符串")
        return self._retrieve(category, query, top_k, issue_type)

    def list_rules(self, category: str) -> list[dict[str, Any]]:
        """Return every enabled general/category rule, independent of relevance."""
        self._validate_arguments(category)
        return self._retrieve(category, "", None, None)

    def _retrieve(
        self, category: str, query: str, top_k: int | None, issue_type: str | None,
    ) -> list[dict[str, Any]]:
        self.last_source = ""
        self.last_warning = ""
        self.last_version = ""
        if self.backend == "local":
            sources = ["local"]
        elif self.backend == "mysql":
            sources = ["mysql", "local"]
        else:
            sources = ["elasticsearch", "mysql", "local"]
        failures = []
        for source in sources:
            try:
                if source == "elasticsearch":
                    rules = self._elasticsearch_rules(category, query, top_k, issue_type)
                else:
                    raw_rules = (self._mysql_rules(category, issue_type) if source == "mysql"
                                 else load_rule_documents(self.rules_path))
                    rules = applicable_rules(raw_rules, category, issue_type)
                    rules = (rank_rules(rules, query, top_k) if top_k is not None
                             else sorted(rules, key=lambda rule: rule["rule_id"]))
                self.last_source = source
                self.last_warning = "; ".join(failures)
                versions = sorted({str(rule.get("version", "")) for rule in rules if rule.get("version")})
                self.last_version = ",".join(versions)
                return rules
            except Exception:
                # Driver/HTTP exceptions may contain credentials, URLs or SQL.
                failures.append(f"{source} 规则检索不可用")
        self.last_warning = "; ".join(failures)
        raise RuleRetrievalError(self.last_warning) from None

    def _mysql_rules(self, category: str, issue_type: str | None) -> list[dict[str, Any]]:
        from sqlalchemy import select
        from db.models import QualityRule
        from db.session import SessionLocal

        statement = select(QualityRule).where(
            QualityRule.category.in_(["通用", category]), QualityRule.status == "enabled",
        )
        if issue_type is not None:
            statement = statement.where(QualityRule.issue_type == issue_type)
        with (self.session_factory or SessionLocal)() as session:
            records = session.scalars(statement.order_by(QualityRule.rule_id)).all()
            return [RuleDocument.model_validate({
                name: getattr(record, name) for name in RuleDocument.model_fields
                if hasattr(record, name)
            }).model_dump(exclude_none=True) for record in records]

    def _elasticsearch_rules(
        self, category: str, query: str, top_k: int | None, issue_type: str | None,
    ) -> list[dict[str, Any]]:
        settings = ElasticsearchSettings.from_env()
        filters: list[dict[str, Any]] = [
            {"terms": {"category": list(dict.fromkeys(["通用", category]))}},
            {"term": {"status": "enabled"}},
        ]
        if issue_type is not None:
            filters.append({"term": {"issue_type": issue_type}})
        boolean: dict[str, Any] = {"filter": filters}
        if query.strip():
            boolean["should"] = [{"multi_match": {
                "query": query, "fields": ["rule_text^2", "bad_examples^3", "rewrite_hint"],
                "type": "best_fields",
            }}]
            # Return applicable rules even if no term matches; matching rules rank first.
            boolean["minimum_should_match"] = 0
        size = top_k or 250
        body: dict[str, Any] = {"query": {"bool": boolean}, "size": size,
                                "sort": [{"rule_id": "asc"}] if top_k is None
                                else [{"_score": "desc"}, {"rule_id": "asc"}]}
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        previous_sort = None
        with managed_client(self.http_client, settings.timeout) as client:
            while True:
                response = client.post(
                    f"{settings.url}/{settings.index}/_search", json=body,
                    headers=settings.headers, auth=settings.auth, timeout=settings.timeout,
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("timed_out") or payload.get("_shards", {}).get("failed", 0):
                    raise RuleRetrievalError("Elasticsearch 返回了不完整的检索结果")
                hits = payload["hits"]["hits"]
                if not isinstance(hits, list):
                    raise RuleRetrievalError("Elasticsearch 检索格式错误")
                for hit in hits:
                    raw_rule = hit["_source"]
                    rule = RuleDocument.model_validate(raw_rule).model_dump(exclude_none=True)
                    if raw_rule.get("source_version"):
                        rule["source_version"] = raw_rule["source_version"]
                    if not applicable_rules([rule], category, issue_type):
                        continue
                    if rule["rule_id"] not in seen:
                        if top_k is not None:
                            rule["retrieval_score"] = float(hit.get("_score") or 0)
                        result.append(rule)
                        seen.add(rule["rule_id"])
                if top_k is not None or len(hits) < size:
                    break
                next_sort = hits[-1].get("sort")
                if not next_sort or next_sort == previous_sort:
                    raise RuleRetrievalError("Elasticsearch 分页游标无效")
                body["search_after"] = next_sort
                previous_sort = next_sort
        return result[:top_k] if top_k is not None else result


def retrieve_rules(
    category: str, query: str, top_k: int = 5, issue_type: str | None = None,
) -> list[dict[str, Any]]:
    return RuleRetriever().retrieve(category, query, top_k, issue_type)
