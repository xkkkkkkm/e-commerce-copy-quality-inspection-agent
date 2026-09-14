import json
from pathlib import Path

from agent.orchestrator import inspect_product
from app.schemas import ProductInput
from tools.quality_checks import required_attribute_checker, risk_score_calculator, title_length_checker


def test_sample_dataset_has_ten_per_category_and_labels():
    path = Path(__file__).parents[1] / "data/samples/products.json"
    samples = json.loads(path.read_text(encoding="utf-8"))
    assert len(samples) == 30
    for category in ("食品", "美妆", "3C"):
        assert sum(item["category"] == category for item in samples) == 10
    assert all("expected_issues" in item for item in samples)


def test_minimum_chain_finds_title_attributes_and_high_risk_terms():
    product = ProductInput(
        product_id="test_001", category="食品",
        title="国家级" + "超长商品标题" * 12,
        description="本产品治疗失眠，100%无副作用。",
        attributes={"brand": "测试"},
    )
    report = inspect_product(product, "task_test")
    issue_types = {issue.issue_type for issue in report.issues}
    assert "标题过长" in issue_types
    assert "关键信息缺失" in issue_types
    assert "医疗功效宣称" in issue_types
    assert report.risk_level == "high"
    assert report.score >= 5


def test_required_attributes_pass_for_complete_3c_product():
    result = required_attribute_checker("3C", {
        "brand": "A", "model": "M1", "specifications": "65W",
        "compatibility": "PD", "warranty": "12个月",
    })
    assert result["passed"] is True
    assert result["issues"] == []


def test_risk_scoring_boundaries():
    assert risk_score_calculator([])["metadata"] == {"score": 0, "risk_level": "pass"}
    assert risk_score_calculator([{"risk_level": "low"}])["metadata"]["risk_level"] == "low"
    assert risk_score_calculator([{"risk_level": "medium"}])["metadata"]["risk_level"] == "medium"
    assert risk_score_calculator([{"risk_level": "high"}])["metadata"]["risk_level"] == "high"


def test_title_length_boundary():
    assert title_length_checker("a" * 60, "3C")["passed"] is True
    assert title_length_checker("a" * 61, "3C")["passed"] is False

