"""Opt in with RUN_MYSQL_TESTS=1; uses real MySQL and adds test tasks."""
import os
from uuid import uuid4

import pytest


pytestmark = pytest.mark.skipif(os.getenv("RUN_MYSQL_TESTS") != "1", reason="requires project MySQL; set RUN_MYSQL_TESTS=1")


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from scripts.seed_data import seed
    from app.admin_auth import get_auth_settings
    monkeypatch.setenv("ADMIN_USERNAME", "api-tests")
    monkeypatch.setenv("ADMIN_PASSWORD", "api-tests-private-password")
    get_auth_settings.cache_clear()

    seed()
    with TestClient(app) as api:
        login = api.post("/api/admin/auth/login", json={"username": "api-tests", "password": "api-tests-private-password"})
        assert login.status_code == 200
        api.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        yield api
        api.post("/api/admin/auth/logout")
    get_auth_settings.cache_clear()


def body():
    return {"product_id": "integration_" + uuid4().hex, "category": "食品", "title": "普通燕麦",
            "description": "本产品治疗失眠。",
            "attributes": {"brand": "谷物", "origin": "山东", "shelf_life": "12个月", "ingredients": "燕麦", "storage": "阴凉干燥处"}}


def test_mysql_result_trace_roundtrip_and_export(client):
    assert client.get("/health").json()["database"] == "mysql"
    response = client.post("/api/products/inspect?mode=rules", json=body())
    assert response.status_code == 200
    report = response.json()
    assert report["risk_level"] == "high"
    task_id = report["task_id"]
    assert client.get(f"/api/results/{task_id}").json() == report
    assert client.get(f"/api/tasks/{task_id}").json()["status"] == "success"
    traces = client.get(f"/api/tasks/{task_id}/traces").json()
    assert any(t["skill_name"] == "risk_expression" and t["tool_name"] == "forbidden_word_checker" for t in traces)
    assert client.get(f"/api/results/{task_id}/export").json()["report"] == report
    assert client.get("/api/tasks/task_nonexistent/traces").status_code == 404


def test_failed_task_is_persisted_and_does_not_poison_next_request(client, monkeypatch):
    import app.main as main
    from sqlalchemy import select
    from db.models import AgentTrace, InspectionTask
    from db.session import SessionLocal

    original = main.inspect_product
    data = body()

    def broken(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(main, "inspect_product", broken)
    assert client.post("/api/products/inspect?mode=rules", json=data).status_code == 500
    with SessionLocal() as db:
        task = db.scalar(select(InspectionTask).where(InspectionTask.product_id == data["product_id"]))
        assert task.status == "failed"
        assert db.scalar(select(AgentTrace).where(AgentTrace.task_id == task.task_id)).status == "failure"
    monkeypatch.setattr(main, "inspect_product", original)
    assert client.post("/api/products/inspect?mode=rules", json=body()).status_code == 200


def test_invalid_input_is_rejected_before_mysql_write(client):
    data = body()
    data["description"] = "😀" * 20000
    assert client.post("/api/products/inspect", json=data).status_code == 422
    data = body()
    data["category"] = "服饰"
    assert client.post("/api/products/inspect", json=data).status_code == 422
