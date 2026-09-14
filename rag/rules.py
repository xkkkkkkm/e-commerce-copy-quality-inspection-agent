"""Shared rule validation and deterministic ranking for fallback retrieval."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


DEFAULT_RULES_PATH = Path(__file__).resolve().parents[1] / "data/rules/rules.json"


class RuleDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    rule_id: str = Field(min_length=1)
    category: Literal["通用", "食品", "美妆", "3C"]
    issue_type: str = Field(min_length=1)
    risk_level: Literal["low", "medium", "high"]
    rule_text: str = Field(min_length=1)
    bad_examples: list[str]
    rewrite_hint: str
    version: str
    status: Literal["enabled", "disabled"]

    # Metadata is attached by the rule adapter and is intentionally optional so
    # older JSON fixtures and Agent consumers remain compatible.
    source_version: str | None = None


def load_rule_documents(path: str | Path = DEFAULT_RULES_PATH) -> list[dict[str, Any]]:
    values = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(values, list):
        raise ValueError("规则文件必须包含 JSON 数组")
    rules = [RuleDocument.model_validate(value).model_dump(exclude_none=True) for value in values]
    ids = [rule["rule_id"] for rule in rules]
    if len(set(ids)) != len(ids):
        raise ValueError("规则文件包含重复 rule_id")
    return rules


def applicable_rules(
    rules: list[dict[str, Any]], category: str, issue_type: str | None = None,
) -> list[dict[str, Any]]:
    return [rule for rule in rules if rule.get("status") == "enabled"
            and rule.get("category") in {"通用", category}
            and (issue_type is None or rule.get("issue_type") == issue_type)]


def rank_rules(rules: list[dict[str, Any]], query: str, top_k: int) -> list[dict[str, Any]]:
    """Use phrases, Latin tokens and Chinese bigrams without a network dependency."""
    normalized = query.casefold()
    tokens = set(re.findall(r"[a-z0-9]+", normalized))
    for text in re.findall(r"[\u4e00-\u9fff]+", normalized):
        tokens.update(text[i:i + 2] for i in range(max(1, len(text) - 1)))

    def score(rule: dict[str, Any]) -> float:
        text = " ".join((rule["issue_type"], rule["rule_text"], rule["rewrite_hint"],
                         *rule["bad_examples"])).casefold()
        phrase_hits = sum(5 for example in rule["bad_examples"]
                          if example and example.casefold() in normalized)
        return phrase_hits + sum(1 for token in tokens if token in text)

    ranked = sorted(rules, key=lambda rule: (-score(rule), rule["rule_id"]))
    return [dict(rule, retrieval_score=float(score(rule))) for rule in ranked[:top_k]]
