from __future__ import annotations

from typing import Any

from app.schemas import InspectionReport, Issue
from tools.quality_checks import (
    forbidden_word_checker,
    json_schema_validator,
    keyword_stuffing_checker,
    required_attribute_checker,
    risk_score_calculator,
    title_core_attribute_checker,
    title_length_checker,
)

from .base import BaseSkill


def _merge(results: list[dict[str, Any]], name: str) -> dict[str, Any]:
    issues = [issue for result in results for issue in result["issues"]]
    return {
        "skill_name": name,
        "passed": not issues,
        "issues": issues,
        "metadata": {"tools": [result["tool_name"] for result in results]},
    }


class TitleQualitySkill(BaseSkill):
    name = "title_quality"
    description = "检查标题长度、关键词堆砌和核心属性。"

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        product = context["product"]
        results = [
            self.tool(context, title_length_checker, product["title"], product["category"]),
            self.tool(context, keyword_stuffing_checker, product["title"]),
            self.tool(context, title_core_attribute_checker, product["title"], product["category"], product.get("attributes", {})),
        ]
        return _merge(results, self.name)


class RiskExpressionSkill(BaseSkill):
    name = "risk_expression"
    description = "检查标题和详情中的违禁词、高风险及绝对化表达。"

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        product = context["product"]
        result = self.tool(context, forbidden_word_checker, product["title"], product.get("description", ""), product.get("attributes", {}))
        return {"skill_name": self.name, "passed": result["passed"], "issues": result["issues"],
                "metadata": {**result["metadata"], "tools": [result["tool_name"]]}}


class RequiredAttributeSkill(BaseSkill):
    name = "required_attribute"
    description = "检查当前类目的必填属性是否完整。"

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        product = context["product"]
        result = self.tool(context, required_attribute_checker, product["category"], product.get("attributes", {}))
        return {"skill_name": self.name, "passed": result["passed"], "issues": result["issues"],
                "metadata": {**result["metadata"], "tools": [result["tool_name"]]}}


class RiskScoringSkill(BaseSkill):
    name = "risk_scoring"
    description = "根据问题风险等级计算总分和风险等级。"

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        result = self.tool(context, risk_score_calculator, context.get("issues", []))
        context["score_result"] = result["metadata"]
        return {"skill_name": self.name, "passed": True, "issues": [],
                "metadata": {**result["metadata"], "tools": [result["tool_name"]]}}


class ReportGenerationSkill(BaseSkill):
    name = "report_generation"
    description = "把 State 中的检查结果组装成统一 InspectionReport JSON。"

    async def run(self, context: dict[str, Any]) -> dict[str, Any]:
        score = context.get("score_result", {"score": 0, "risk_level": "pass"})
        report = InspectionReport(
            task_id=context["task_id"], status="partial" if context.get("required_failed") else "success", risk_level=score["risk_level"],
            score=score["score"], issues=[Issue(**issue) for issue in context.get("issues", [])],
            optimized_title=context.get("optimized_title", context["product"]["title"]),
            optimized_description=context.get("optimized_description", context["product"].get("description", "")),
            category=context.get("category"), summary=context.get("summary", ""),
            rewrite_reason=context.get("rewrite_reason", ""), rules=context.get("rules", []),
            warnings=context.get("warnings", []), degraded=bool(context.get("warnings")),
            mode=context.get("mode", "rules"), retrieval_source=context.get("retrieval_source", "local"),
            model_used=context.get("model_used", False),
        )
        validation = self.tool(context, json_schema_validator, report.model_dump())
        return {"skill_name": self.name, "passed": validation["passed"], "issues": validation["issues"],
                "metadata": {"report": report.model_dump(), "tools": [validation["tool_name"]],
                             **validation["metadata"]}}
