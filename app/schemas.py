import math
from typing import Any, Literal

import json
from pydantic import BaseModel, ConfigDict, Field, field_validator


Category = Literal["食品", "美妆", "3C"]
RiskLevel = Literal["pass", "low", "medium", "high"]


class ProductInput(BaseModel):
    product_id: str = Field(min_length=1, max_length=128)
    category: Category | None = None
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=20_000)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("attributes", mode="before")
    @classmethod
    def bounded_attributes(cls, value: Any) -> dict[str, Any]:
        """Keep arbitrary attributes JSON-compatible and bounded before storage."""
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("attributes必须是JSON对象")
        max_depth, max_keys, max_string_bytes = 8, 512, 16_384
        key_count = 0

        def walk(node: Any, depth: int) -> Any:
            nonlocal key_count
            if depth > max_depth:
                raise ValueError("attributes嵌套层级不能超过8层")
            if isinstance(node, dict):
                out = {}
                for key, item in node.items():
                    if not isinstance(key, str) or not key.strip() or len(key) > 256:
                        raise ValueError("attributes键必须为非空短字符串")
                    key_count += 1
                    if key_count > max_keys:
                        raise ValueError("attributes键数量不能超过512")
                    out[key] = walk(item, depth + 1)
                return out
            if isinstance(node, list):
                if len(node) > max_keys:
                    raise ValueError("attributes数组长度不能超过512")
                return [walk(item, depth + 1) for item in node]
            if isinstance(node, str):
                if len(node.encode("utf-8")) > max_string_bytes:
                    raise ValueError("attributes字符串不能超过16384字节")
                return node
            if isinstance(node, (bool, int)):
                return node
            if isinstance(node, float):
                if not math.isfinite(node):
                    raise ValueError("attributes不能包含非有限数字")
                return node
            if node is None:
                return None
            raise ValueError("attributes只能包含JSON兼容值")

        result = walk(value, 0)
        try:
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("attributes必须是有效JSON") from exc
        if len(encoded) > 128 * 1024:
            raise ValueError("attributes JSON大小不能超过128KiB")
        return result

    @field_validator("product_id", "title")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value

    @field_validator("description")
    @classmethod
    def description_storage_limit(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 60_000:
            raise ValueError("详情文案UTF-8编码后不能超过60000字节，请缩短内容。")
        return value


class Issue(BaseModel):
    issue_type: str
    field: str
    risk_level: RiskLevel
    evidence: str
    suggestion: str
    rule_id: str | None = None
    matched_text: str | None = None
    source: str = "rule"


class InspectionReport(BaseModel):
    task_id: str
    status: Literal["success", "partial", "failed"]
    risk_level: RiskLevel
    score: int = Field(ge=0)
    issues: list[Issue]
    optimized_title: str
    optimized_description: str
    category: Category | None = None
    summary: str = ""
    rewrite_reason: str = ""
    rules: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    degraded: bool = False
    mode: Literal["rules", "full"] = "rules"
    retrieval_source: str = "local"
    model_used: bool = False
    rule_version: str = ""
    rule_source: str = ""


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CategoryDecision(StrictOutput):
    category: Category | None


class SemanticIssue(StrictOutput):
    issue_type: str = Field(min_length=1, max_length=128)
    field: Literal["title", "description"]
    risk_level: Literal["low", "medium", "high"]
    evidence: str = Field(min_length=1, max_length=2000)
    suggestion: str = Field(min_length=1, max_length=2000)
    rule_id: str = Field(min_length=1)


class SemanticOutput(StrictOutput):
    issues: list[SemanticIssue] = Field(max_length=30)


class CopyOutput(StrictOutput):
    optimized_title: str = Field(min_length=1, max_length=500)
    optimized_description: str = Field(max_length=20000)
    reason: str = Field(min_length=1, max_length=2000)


class SummaryOutput(StrictOutput):
    summary: str = Field(min_length=1, max_length=2000)


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    task_id: str
    product_id: str
    status: str
    trigger_source: str
    error_message: str | None
