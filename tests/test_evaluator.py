import json
from collections import Counter
from pathlib import Path

import pytest

from evaluator.run_eval import (
    ROOT, api_inspector, compute_metrics, load_cases, product_payload, run_evaluation, write_report,
)


def fixture(case_id, issues=None, risk="pass", category="食品"):
    return {
        "case_id": case_id, "product_id": case_id, "category": category,
        "title": "示例商品", "description": "", "attributes": {},
        "expected_issues": issues or [], "expected_risk_level": risk,
    }


def response(task_id, issues=None, risk="pass", status="success"):
    return {
        "task_id": task_id, "status": status, "risk_level": risk, "score": 0,
        "issues": [{"issue_type": issue, "field": "description", "risk_level": "high",
                    "evidence": "fixture evidence", "suggestion": "review"} for issue in issues or []],
        "optimized_title": "示例商品", "optimized_description": "",
    }


def test_issue_micro_denominators_and_duplicate_evidence():
    fixtures = [fixture("a", ["A", "B"], "high"), fixture("b")]
    def inspect(product, task_id):
        return response(task_id, ["A", "A", "C"] if product.product_id == "a" else ["C"], "high")
    summary = run_evaluation(fixtures, inspect)["summary"]
    assert (summary["issue_tp"], summary["issue_fp"], summary["issue_fn"]) == (1, 2, 1)
    assert summary["issue_recall"] == 0.5
    assert summary["issue_precision"] == pytest.approx(1 / 3, abs=1e-6)
    assert summary["false_discovery_rate"] == pytest.approx(2 / 3, abs=1e-6)
    assert summary["high_risk_precision"] == 0.5
    assert summary["high_risk_recall"] == 1
    assert summary["risk_accuracy"] == 0.5


def test_empty_denominators_are_undefined():
    empty = compute_metrics([])
    assert empty["case_count"] == 0
    for metric in ("issue_recall", "false_discovery_rate", "high_risk_precision", "high_risk_recall",
                   "risk_accuracy", "structured_output_success_rate", "average_latency_ms"):
        assert empty[metric] is None
    negative = run_evaluation([fixture("negative")], lambda product, task_id: response(task_id))["summary"]
    assert negative["issue_recall"] is None
    assert negative["false_discovery_rate"] is None
    assert negative["strict_case_accuracy"] == 1
    assert negative["risk_accuracy"] == 1


def test_call_error_is_a_false_negative_and_does_not_abort_next_case():
    called = []
    def inspect(product, task_id):
        called.append(product.product_id)
        if product.product_id == "broken":
            raise TimeoutError("fixture timeout")
        return response(task_id)
    report = run_evaluation([fixture("broken", ["A"], "high"), fixture("ok")], inspect)
    assert called == ["broken", "ok"]
    assert report["summary"]["issue_fn"] == 1
    assert report["summary"]["high_risk_recall"] == 0
    assert report["summary"]["structured_output_success_rate"] == 0.5
    assert report["summary"]["risk_accuracy"] == 0.5
    assert report["failures"][0]["missing_issues"] == ["A"]
    assert "TimeoutError" in report["failures"][0]["error"]
    assert all(case["latency_ms"] >= 0 for case in report["cases"])


def test_invalid_structure_and_failed_status_have_distinct_success_metrics():
    fixtures = [fixture("invalid"), fixture("failed", ["A"], "high")]
    def inspect(product, task_id):
        if product.product_id == "invalid":
            return {"unexpected": True}
        return response(task_id, ["A"], "high", status="failed")
    report = run_evaluation(fixtures, inspect)
    assert report["summary"]["structured_output_success_rate"] == 0.5
    assert report["summary"]["execution_success_rate"] == 0
    assert report["summary"]["issue_fn"] == 1
    assert report["summary"]["risk_accuracy"] == 0
    assert report["summary"]["error_count"] == 2


def test_category_metrics_use_their_own_denominators():
    fixtures = [fixture("f", ["A", "B"], "high"), fixture("b", ["C"], "medium", "美妆")]
    def inspect(product, task_id):
        return response(task_id, ["A"] if product.category == "食品" else ["C"],
                        "high" if product.category == "食品" else "medium")
    report = run_evaluation(fixtures, inspect)
    assert report["summary"]["issue_recall"] == pytest.approx(2 / 3, abs=1e-6)
    assert report["by_category"]["食品"]["issue_recall"] == 0.5
    assert report["by_category"]["美妆"]["issue_recall"] == 1
    assert report["by_category"]["美妆"]["high_risk_precision"] is None
    assert report["failures"][0]["missing_issues"] == ["B"]


def test_full_mode_without_model_is_not_presented_as_llm_evaluation():
    def inspect(product, task_id):
        return {**response(task_id), "degraded": True, "model_used": False}
    report = run_evaluation([fixture("fallback")], inspect, mode="full")
    assert report["summary"]["degraded_case_count"] == 1
    assert report["summary"]["model_used_case_count"] == 0
    assert report["config"]["model_evaluation_status"] == "no_model_usage_reported"
    assert "不能作为真实LLM评测" in report["config"]["model_evaluation_note"]


def test_written_reports_are_readable_and_reject_colliding_extensions(tmp_path):
    report = run_evaluation([fixture("saved")], lambda product, task_id: response(task_id))
    path = tmp_path / "report.json"
    path.write_text("previous report", encoding="utf-8")
    json_path, markdown_path = write_report(report, path)
    assert json.loads(json_path.read_text(encoding="utf-8"))["run_id"] == report["run_id"]
    assert report["run_id"] in markdown_path.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))
    with pytest.raises(ValueError, match=".json"):
        write_report(report, tmp_path / "report.md")


def test_fixtures_preserve_legacy_labels_and_required_distribution():
    cases = load_cases()
    assert len(cases) == 50
    assert Counter(case["category"] for case in cases) == {"食品": 20, "美妆": 15, "3C": 15}
    assert {case["expected_risk_level"] for case in cases} == {"high", "medium", "low", "pass"}
    originals = json.loads((ROOT / "data/samples/products.json").read_text(encoding="utf-8"))
    by_id = {case["product_id"]: case for case in cases}
    for original in originals:
        assert {key: by_id[original["product_id"]][key] for key in original} == original
    assert len(by_id["food_017"]["title"]) == 61
    assert len(by_id["food_018"]["title"]) == 60


def test_gold_metadata_never_leaks_into_product_input():
    item = fixture("gold", ["A"], "high")
    assert set(product_payload(item)) == {"product_id", "category", "title", "description", "attributes"}
    assert product_payload({"product_json": item}) == product_payload(item)


def test_api_mode_is_explicit_and_request_excludes_gold(monkeypatch):
    captured = {}
    class Context:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, json):
            import httpx
            captured.update(url=url, body=json, method="POST")
            return httpx.Response(200, json=response("server_generated_task"), request=httpx.Request("POST", url))
    def request(origin, timeout):
        captured.update(timeout=timeout)
        return Context()
    monkeypatch.setattr("scripts.api_session.authenticated_client", request)
    report = run_evaluation([fixture("api")], api_inspector("http://localhost:8000", "rules", 3))
    assert captured["url"] == "http://localhost:8000/api/products/inspect?mode=rules"
    assert captured["method"] == "POST"
    assert "expected_risk_level" not in captured["body"]
    assert captured["timeout"] == 3
    assert report["cases"][0]["task_id"] == "server_generated_task"


def test_load_cases_rejects_implicit_gold(tmp_path: Path):
    item = fixture("missing")
    del item["expected_risk_level"]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps([item]), encoding="utf-8")
    with pytest.raises(ValueError, match="explicitly annotated"):
        load_cases(path)
