"""Offline generator, scenario and CLI contracts; no database or LLM required."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.catalog_schemas import ProductCreate
from services.simulation import DeliveryRequest, SimulationRequest, generate_preview
from tools.category_checks import category_specific_checker
from tools.quality_checks import all_checks


ROOT = Path(__file__).resolve().parents[1]


def test_reproducibility_provenance_and_category_scenario_coverage():
    request = SimulationRequest(count=100, seed=2026, batch_id="coverage-2026")
    result = generate_preview(request)
    assert result == generate_preview(request.model_dump())
    assert result["source"] == "synthetic"
    assert result["summary"]["merchant_count"] == 9
    assert set(result["summary"]["categories"]) == {"食品", "美妆", "3C"}
    assert set(result["summary"]["scenarios"]) == {"clean", "risky", "missing", "conflict"}
    assert len({x["product"]["product_id"] for x in result["products"]}) == 100
    assert len({x["product"]["title"] for x in result["products"]}) > 45
    assert {(x["product"]["category"], x["provenance"]["scenario"]) for x in result["products"]} == {
        (category, scenario) for category in ("食品", "美妆", "3C")
        for scenario in ("clean", "risky", "missing", "conflict")
    }
    for index, entry in enumerate(result["products"], start=1):
        ProductCreate.model_validate(entry["product"])
        assert entry["product"]["attributes"]["_simulation"] == entry["provenance"]
        assert entry["provenance"]["ordinal"] == index
        assert entry["provenance"]["generator_version"] == result["generator_version"]
        assert entry["product"]["merchant_name"].startswith("模拟·")
        assert not {"status", "risk_level", "inspection", "expected_risk", "gold"}.intersection(entry)
    changed = generate_preview({**request.model_dump(), "seed": 2027})
    assert {x["product"]["product_id"] for x in result["products"]}.isdisjoint(
        x["product"]["product_id"] for x in changed["products"])


def test_changed_options_keep_ids_so_delivery_can_detect_conflicts():
    original = generate_preview({"count": 12, "batch_id": "same-identity", "scenario": "clean"})
    changed = generate_preview({"count": 12, "batch_id": "same-identity", "scenario": "risky"})
    assert [x["product"]["product_id"] for x in original["products"]] == [x["product"]["product_id"] for x in changed["products"]]
    assert original["products"][0]["product"] != changed["products"][0]["product"]
    more = generate_preview({"count": 13, "batch_id": "same-identity", "scenario": "clean"})
    assert original["products"][0]["product"]["product_id"] == more["products"][0]["product"]["product_id"]
    assert original["products"][0]["product"] != more["products"][0]["product"]


@pytest.mark.parametrize("scenario,expected", [("clean", None), ("risky", "high"),
                                              ("missing", "关键信息缺失"), ("conflict", "属性与文案冲突")])
def test_generated_scenarios_exercise_real_rule_checks(scenario, expected):
    result = generate_preview({"count": 100, "seed": 73, "scenario": scenario})
    for entry in result["products"]:
        checks = [*all_checks(entry["product"]), category_specific_checker(entry["product"])]
        issues = [issue for check in checks for issue in check["issues"]]
        assert not any("_simulation" in issue["field"] for issue in issues)
        if expected is None:
            assert issues == [], (entry["product"], issues)
        elif scenario == "risky":
            assert any(issue["risk_level"] == expected for issue in issues)
        else:
            assert {issue["issue_type"] for issue in issues} == {expected}


@pytest.mark.parametrize("change", [
    {"count": 0}, {"count": 101}, {"count": True}, {"count": 1.5}, {"count": "30"},
    {"seed": -1}, {"seed": 2**32}, {"seed": False}, {"seed": "42"},
    {"scenario": "passed"}, {"batch_id": ""}, {"batch_id": "x" * 65},
    {"batch_id": "../demo"}, {"batch_id": "has space"}, {"batch_id": "治疗"},
    {"batch_id": "-demo"}, {"batch_id": "demo\n"}, {"status": "published"},
])
def test_strict_request_validation(change):
    with pytest.raises(ValidationError):
        SimulationRequest.model_validate(change)


def test_delivery_flag_strict_and_metadata_independent_copies():
    with pytest.raises(ValidationError):
        DeliveryRequest(enqueue_inspection="false")
    result = generate_preview({"count": 1})
    result["products"][0]["provenance"]["source"] = "changed"
    assert result["products"][0]["product"]["attributes"]["_simulation"]["source"] == "synthetic"
    assert generate_preview({"count": 1})["source"] == "synthetic"


def test_cli_export_is_reproducible_without_database_or_credentials():
    env = {key: value for key, value in os.environ.items()
           if key not in {"ADMIN_USERNAME", "ADMIN_PASSWORD", "LLM_API_KEY", "OPENAI_API_KEY"}}
    env["DATABASE_URL"] = "database-must-not-be-imported"
    command = [sys.executable, "-m", "scripts.simulate_ingestion", "--count", "12", "--seed", "11", "--batch-id", "cli-demo"]
    first = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=True)
    second = subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, check=True)
    assert first.stdout == second.stdout and first.stderr == ""
    assert json.loads(first.stdout) == generate_preview({"count": 12, "seed": 11, "batch_id": "cli-demo"})
    assert subprocess.run([sys.executable, "-m", "scripts.simulate_ingestion", "--help"],
                          cwd=ROOT, env=env, text=True, capture_output=True).returncode == 0
    rejected = subprocess.run([*command, "--count", "101"], cwd=ROOT, env=env, text=True, capture_output=True)
    assert rejected.returncode == 2 and not rejected.stdout


def test_generation_does_not_import_database_modules():
    script = ("import sys; from services.simulation import generate_preview; "
              "generate_preview({'count': 1}); "
              "assert 'db.session' not in sys.modules; assert 'sqlalchemy' not in sys.modules")
    subprocess.run([sys.executable, "-c", script], cwd=ROOT, check=True)


def test_cli_delivery_uses_authenticated_api_and_logs_out(monkeypatch):
    import httpx
    import dotenv
    from scripts.simulate_ingestion import deliver_via_api

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("ADMIN_USERNAME", "simulation-cli-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "simulation-cli-test-secret")
    observed = []

    def handle(request):
        observed.append(request.url.path)
        if request.url.path.endswith("/login"):
            assert json.loads(request.content) == {"username": "simulation-cli-test", "password": "simulation-cli-test-secret"}
            return httpx.Response(200, json={"csrf_token": "test-csrf"}, headers={"Set-Cookie": "admin_session=test-session; Path=/"})
        assert request.headers["X-CSRF-Token"] == "test-csrf"
        assert "admin_session=test-session" in request.headers["cookie"]
        if request.url.path.endswith("/deliver"):
            assert json.loads(request.content)["enqueue_inspection"] is False
            return httpx.Response(200, json={"created_count": 12, "job_id": None})
        return httpx.Response(200, json={"status": "ok"})

    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs))
    result = deliver_via_api(SimulationRequest(count=12), "http://simulation.test", False)
    assert result == {"created_count": 12, "job_id": None}
    assert observed == ["/api/admin/auth/login", "/api/admin/simulation/deliver", "/api/admin/auth/logout"]


def test_cli_delivery_failure_does_not_echo_sensitive_response(monkeypatch):
    import httpx
    import dotenv
    from scripts.simulate_ingestion import deliver_via_api

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("ADMIN_USERNAME", "simulation-cli-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-do-not-print")
    observed = []

    def handle(request):
        observed.append(request.url.path)
        if request.url.path.endswith("/login"):
            return httpx.Response(200, json={"csrf_token": "test-csrf"})
        if request.url.path.endswith("/deliver"):
            return httpx.Response(409, text="secret-do-not-print")
        return httpx.Response(200, json={"status": "ok"})

    original_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original_client(transport=httpx.MockTransport(handle), **kwargs))
    with pytest.raises(RuntimeError) as error:
        deliver_via_api(SimulationRequest(count=12), "http://simulation.test", False)
    assert "409" in str(error.value) and "secret-do-not-print" not in str(error.value)
    assert observed[-1].endswith("/logout")
