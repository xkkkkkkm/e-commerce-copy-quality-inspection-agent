import asyncio
import json
from pathlib import Path

import pytest

from agent.orchestrator import AgentOrchestrator, inspect_product
from app.schemas import CopyOutput, ProductInput, SemanticOutput, SummaryOutput
from rag import RuleRetriever
from skills.copy_optimization import safe_copy
from tools.quality_checks import forbidden_word_checker


def product(**changes):
    values = dict(product_id="fixture", category="食品", title="普通燕麦 500g",
                  description="早餐冲泡食用。",
                  attributes={"brand": "谷物", "origin": "山东", "shelf_life": "12个月",
                              "ingredients": "燕麦", "storage": "阴凉干燥处"})
    return ProductInput(**{**values, **changes})


class FakeModel:
    enabled = True

    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.calls = []

    def generate_json(self, purpose, payload, schema):
        self.calls.append(purpose)
        if purpose in self.overrides:
            value = self.overrides[purpose]
            if isinstance(value, Exception):
                raise value
            return schema.model_validate(value)
        if purpose == "semantic":
            return SemanticOutput(issues=[])
        if purpose == "optimization":
            return CopyOutput(optimized_title=payload["permitted_copy"]["optimized_title"],
                              optimized_description=payload["permitted_copy"]["optimized_description"],
                              reason="删除风险分句，保留原有商品事实。")
        if purpose == "summary":
            return SummaryOutput(summary=payload["permitted_summary"])
        return schema.model_validate({"category": "食品"})


def test_full_mode_calls_models_and_preserves_verified_rule_evidence():
    traces = []
    llm = FakeModel()
    report = inspect_product(product(description="本产品治疗失眠。早餐冲泡食用。"), "t",
                             traces.append, mode="full", llm=llm, retriever=RuleRetriever("local"))
    assert report.risk_level == "high"
    assert report.model_used and not report.degraded
    assert {"semantic", "optimization", "summary"} <= set(llm.calls)
    assert report.optimized_description == "早餐冲泡食用。"
    assert not forbidden_word_checker(report.optimized_title, report.optimized_description)["issues"]
    assert all(issue.rule_id in {r["rule_id"] for r in report.rules} for issue in report.issues)
    assert any(t["tool_name"] == "deepseek.semantic" for t in traces)
    assert any(t["skill_name"] == "title_quality" and t["tool_name"] == "title_length_checker" for t in traces)


@pytest.mark.parametrize("bad", [
    {"issues": [{"issue_type": "医疗功效宣称", "field": "description", "risk_level": "high",
                 "evidence": "编造的原文", "suggestion": "删除", "rule_id": "food_claim_001"}]},
    {"issues": [{"issue_type": "医疗功效宣称", "field": "description", "risk_level": "high",
                 "evidence": "本产品治疗失眠。", "suggestion": "删除", "rule_id": "fake_rule"}]},
    {"issues": "bad"},
    TimeoutError("timeout"),
])
def test_invalid_or_failed_model_does_not_discard_rule_results(bad):
    llm = FakeModel({"semantic": bad})
    report = inspect_product(product(description="本产品治疗失眠。"), "t", mode="full",
                             llm=llm, retriever=RuleRetriever("local"))
    assert report.risk_level == "high" and report.degraded
    assert any("semantic_risk" in warning for warning in report.warnings)


def test_cross_clause_semantic_evidence_is_removed():
    context = {"product": product(description="每天一杯，血糖从此无忧。早餐冲泡食用。").model_dump(),
               "issues": [{"field": "description", "matched_text": "每天一杯，血糖从此无忧。"}]}
    assert safe_copy(context)["optimized_description"] == "早餐冲泡食用。"


def test_model_cannot_invent_capability_in_copy_or_override_summary():
    llm = FakeModel({
        "optimization": {"optimized_title": "燕麦", "optimized_description": "可防癌", "reason": "新增"},
        "summary": {"summary": "完全合规，没有风险。"},
    })
    report = inspect_product(product(description="治疗失眠。"), "t", mode="full",
                             llm=llm, retriever=RuleRetriever("local"))
    assert "防癌" not in report.optimized_description
    assert "high" in report.summary and report.degraded


def test_rewrite_explanation_cannot_add_model_claims():
    class BadReason(FakeModel):
        def generate_json(self, purpose, payload, schema):
            result = super().generate_json(purpose, payload, schema)
            if purpose == "optimization":
                result.reason = "产品已获国家级认证，治疗失眠无副作用。"
            return result

    report = inspect_product(product(description="治疗失眠。"), "t", mode="full",
                             llm=BadReason(), retriever=RuleRetriever("local"))
    assert "国家级" not in report.rewrite_reason


def test_description_fits_mysql_utf8mb4_text():
    assert len(product(description="中" * 20000).description) == 20000
    with pytest.raises(ValueError, match="60000"):
        product(description="😀" * 20000)


def test_rules_pass_keeps_copy_and_reports_real_tool_outcomes():
    traces = []
    data = product()
    report = inspect_product(data, "t", traces.append)
    assert report.risk_level == "pass" and not report.degraded
    assert report.optimized_title == data.title
    assert report.optimized_description == data.description
    assert not any(t["tool_name"] and t["tool_name"].startswith("deepseek") for t in traces)
    assert all("issues=0" in t["output_summary"] for t in traces if t["tool_name"] and t["tool_name"].endswith("checker"))


def test_retrieval_failure_does_not_abort_rules():
    class BrokenRetriever:
        def retrieve(self, *args, **kwargs):
            raise ConnectionError("unavailable")

    report = inspect_product(product(description="治疗失眠"), "t", retriever=BrokenRetriever())
    assert report.risk_level == "high" and report.degraded
    assert report.retrieval_source == "unavailable"


def test_async_entry_and_category_inference():
    report = asyncio.run(AgentOrchestrator().arun(product(category=None), "async_task"))
    assert report.category == "食品" and report.status == "success"
    with pytest.raises(ValueError, match="无法可靠识别"):
        inspect_product(ProductInput(product_id="u", title="未知商品"), "u")


def test_sink_type_error_is_not_retried():
    calls = []

    def sink(**event):
        calls.append(event)
        raise TypeError("sink internal error")

    report = inspect_product(product(), "t", sink)
    assert len({t["step_name"] for t in calls}) == len(calls)
    assert report.degraded and any("Trace" in w for w in report.warnings)


def test_all_gold_cases_have_valid_reports_and_safe_copies():
    cases = json.loads((Path(__file__).parents[1] / "data/evaluation/cases.json").read_text())
    for item in cases:
        report = inspect_product(ProductInput.model_validate(item), item["product_id"])
        assert report.status == "success"
        assert all(issue.rule_id for issue in report.issues), item["product_id"]
        assert not forbidden_word_checker(report.optimized_title, report.optimized_description)["issues"], item["product_id"]
