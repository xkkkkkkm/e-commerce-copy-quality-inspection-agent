import asyncio

from agent.orchestrator import AgentOrchestrator, InspectionState
from app.schemas import ProductInput
from skills import CategoryRouter
from skills.general import (
    ReportGenerationSkill,
    RequiredAttributeSkill,
    RiskExpressionSkill,
    RiskScoringSkill,
    TitleQualitySkill,
)
from tools.quality_checks import (
    attribute_consistency_checker,
    forbidden_word_checker,
    json_schema_validator,
    keyword_stuffing_checker,
    required_attribute_checker,
    risk_score_calculator,
    title_core_attribute_checker,
    title_length_checker,
)


def run(skill, context):
    return asyncio.run(skill.run(context))


def test_required_week2_tools_have_uniform_contract():
    product = {
        "category": "3C", "title": "品牌A 型号M2",
        "description": "型号：M9，品牌：品牌A。", "attributes": {
            "brand": "品牌A", "model": "M2", "compatibility": "USB-C",
        },
    }
    for result in (
        attribute_consistency_checker(product),
        json_schema_validator({"bad": True}),
    ):
        assert {"passed", "issues", "metadata", "tool_name"} <= result.keys()
    assert attribute_consistency_checker(product)["passed"] is False
    assert json_schema_validator({"bad": True})["passed"] is False


def test_every_week2_tool_returns_uniform_contract():
    results = [
        title_length_checker("商品", "食品"),
        keyword_stuffing_checker("旗舰旗舰商品"),
        forbidden_word_checker("商品", "绝对有效"),
        required_attribute_checker("食品", {}),
        title_core_attribute_checker("商品", "食品", {}),
        attribute_consistency_checker({"title": "商品", "description": "", "attributes": {}}),
        risk_score_calculator([]),
        json_schema_validator({"bad": True}),
    ]
    assert all({"tool_name", "passed", "issues", "metadata"} <= result.keys() for result in results)


def test_general_skills_share_base_contract():
    product = ProductInput(product_id="p1", category="食品", title="治疗失眠" , description="100%有效")
    context = {"task_id": "t1", "product": product.model_dump(), "category": "食品", "issues": []}
    for skill in (TitleQualitySkill(), RiskExpressionSkill(), RequiredAttributeSkill(), RiskScoringSkill(), ReportGenerationSkill()):
        result = run(skill, context)
        assert {"skill_name", "passed", "issues", "metadata"} <= result.keys()


def test_router_selects_category_skill():
    router = CategoryRouter()
    assert router.route({"category": "食品"}).name == "food_category"
    assert router.route({"category": "美妆"}).name == "beauty_category"
    assert router.route({"category": "3C"}).name == "3c_category"


def test_orchestrator_state_flow_and_trace_steps():
    traces = []
    product = ProductInput(product_id="p2", category="食品", title="国家级好茶", description="治疗失眠")
    report = AgentOrchestrator(trace_sink=traces.append).run(product, "task_p2")
    assert report.task_id == "task_p2"
    assert report.risk_level == "high"
    assert {item["step_name"] for item in traces} >= {
        "normalize_input", "category_router", "rule_retriever", "title_quality",
        "risk_expression", "required_attribute", "food_category", "risk_scoring",
        "copy_optimization", "report_generation", "persist_trace",
    }


def test_skill_failure_is_isolated():
    class BrokenSkill(TitleQualitySkill):
        name = "broken"

        async def run(self, context):
            raise RuntimeError("expected")

    traces = []
    agent = AgentOrchestrator(trace_sink=traces.append)
    agent.general_skills = (BrokenSkill(), RiskExpressionSkill())
    report = agent.run(ProductInput(product_id="p3", category="食品", title="普通商品"), "task_p3")
    assert report.status == "partial"
    assert any(item["step_name"] == "broken" and item["status"] == "failure" for item in traces)
