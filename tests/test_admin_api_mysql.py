"""Real MySQL, HTTP authentication and catalog workflow integration.

RUN_MYSQL_TESTS=1 enables these tests. Only this fixture's generated records are
removed; existing catalog products and historical user reports are preserved.
"""
import os
from uuid import uuid4

import pytest


pytestmark = pytest.mark.skipif(os.getenv("RUN_MYSQL_TESTS") != "1", reason="requires project MySQL")


@pytest.fixture
def admin_client(monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import delete, select
    from app.main import app
    from app.admin_auth import get_auth_settings
    from db.catalog_models import ManagedProduct, ProductAudit, ProductInspection, ProductRevision
    from db.models import AgentTrace, InspectionResult, InspectionTask
    from db.session import SessionLocal
    from scripts.seed_data import seed

    monkeypatch.setenv("ADMIN_USERNAME", "admin-api-test")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-api-test-password")
    monkeypatch.setenv("ADMIN_COOKIE_SECURE", "false")
    get_auth_settings.cache_clear()
    seed()
    product_ids = []
    with TestClient(app) as client:
        assert client.get("/api/admin/products").status_code == 401
        login = client.post("/api/admin/auth/login", json={"username": "admin-api-test", "password": "admin-api-test-password"})
        assert login.status_code == 200
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]

        def create(**overrides):
            payload = {"product_id": "admin_api_" + uuid4().hex, "merchant_name": "集成测试商家",
                       "category": "食品", "title": "谷物原味燕麦片500克",
                       "description": "原味燕麦片，配料为燕麦。开封后请密封并于阴凉干燥处保存。",
                       "attributes": {"brand": "谷物", "origin": "山东", "shelf_life": "12个月",
                                      "ingredients": "燕麦", "storage": "阴凉干燥处"}}
            payload.update(overrides)
            product_ids.append(payload["product_id"])
            response = client.post("/api/admin/products", json=payload)
            assert response.status_code == 201, response.text
            return response.json(), payload

        yield client, create
        client.post("/api/admin/auth/logout")
    get_auth_settings.cache_clear()
    with SessionLocal() as db:
        ids = list(db.scalars(select(ManagedProduct.id).where(ManagedProduct.product_id.in_(product_ids))))
        task_ids = list(db.scalars(select(InspectionTask.task_id).where(InspectionTask.product_id.in_(product_ids))))
        for model in (ProductAudit, ProductInspection, ProductRevision):
            db.execute(delete(model).where(model.managed_product_id.in_(ids)))
        db.execute(delete(ManagedProduct).where(ManagedProduct.id.in_(ids)))
        for model in (AgentTrace, InspectionResult, InspectionTask):
            db.execute(delete(model).where(model.task_id.in_(task_ids)))
        db.commit()


def test_edit_inspect_publish_history_and_logout(admin_client):
    client, create = admin_client
    product, payload = create()
    path = f"/api/admin/products/{product['id']}"
    assert client.get("/api/admin/products", params={"q": payload["product_id"]}).json()["total"] == 1
    assert client.post(path + "/actions/publish", json={"expected_version": 1}).status_code == 409
    inspection = client.post(path + "/inspect", json={"expected_version": 1, "mode": "rules"})
    assert inspection.status_code == 200, inspection.text
    result = inspection.json()
    assert result["report"]["risk_level"] == "pass"
    assert result["product"]["inspection_complete"] is True
    task_id = result["report"]["task_id"]
    assert client.get(f"/api/admin/inspections/{task_id}/report").json() == result["report"]
    assert client.get(f"/api/admin/inspections/{task_id}/traces").json()
    assert client.post(path + "/actions/publish", json={"expected_version": 1, "expected_status": "pending",
                                                          "expected_inspection_id": result["inspection"]["id"]}).json()["status"] == "published"
    edited = client.put(path, json={**payload, "description": "本品治疗失眠。", "expected_version": 1})
    assert edited.status_code == 200
    changed = edited.json()
    assert changed["version"] == 2 and changed["status"] == "pending"
    assert not changed["inspection_fresh"] and changed["latest_report"] is None
    assert len(changed["revisions"]) == 2 and len(changed["inspections"]) == 1
    assert {entry["action"] for entry in changed["audits"]} >= {"create", "inspect_complete", "publish", "edit"}
    assert client.post(path + "/actions/publish", json={"expected_version": 2}).status_code == 409
    assert client.put(path, json={**payload, "expected_version": 1}).status_code == 409
    assert client.post(path + "/inspect", json={"expected_version": 2}).json()["report"]["risk_level"] == "high"
    assert client.post(path + "/actions/publish", json={"expected_version": 2, "reason": "不能绕过高风险"}).status_code == 409
    assert client.post(path + "/actions/reject", json={"expected_version": 2}).status_code == 422
    assert client.post(path + "/actions/reject", json={"expected_version": 2, "reason": "请删除治疗承诺"}).json()["status"] == "rejected"
    assert client.post(path + "/actions/submit", json={"expected_version": 2}).json()["status"] == "pending"
    assert client.post("/api/admin/auth/logout").status_code == 200
    assert client.get(path).status_code == 401
    # Legacy report URLs must not bypass management authentication.
    for route in (f"/api/tasks/{task_id}", f"/api/tasks/{task_id}/traces",
                  f"/api/results/{task_id}", f"/api/results/{task_id}/export"):
        assert client.get(route).status_code == 401


def test_batch_isolation_and_csrf(admin_client):
    client, create = admin_client
    product, _ = create()
    stale, _ = create()
    body = {"items": [{"id": stale["id"], "expected_version": 999},
                      {"id": product["id"], "expected_version": 1}], "mode": "rules"}
    token = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/admin/products/batch-inspect", json=body).status_code == 403
    client.headers["X-CSRF-Token"] = token
    response = client.post("/api/admin/products/batch-inspect", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["success_count"], result["failed_count"]) == (1, 1)
    assert not result["items"][0]["ok"] and result["items"][1]["ok"]
    assert client.post("/api/admin/products/batch-inspect", json={"items": [body["items"][1]] * 2}).status_code == 422
    assert client.get("/api/admin/summary").json()["total"] >= 2
    path = f"/api/admin/products/{product['id']}/actions"
    detail = client.get(f"/api/admin/products/{product['id']}").json()
    inspection_id = detail["latest_inspection_id"]
    assert client.post(path + "/publish", json={"expected_version": 1,
                                                  "expected_inspection_id": inspection_id}).status_code == 200
    assert client.post(path + "/offline", json={"expected_version": 1}).json()["status"] == "offline"
    assert client.post(path + "/publish", json={"expected_version": 1,
                                                  "expected_inspection_id": inspection_id}).json()["status"] == "published"


def test_batch_publication_preview_partial_success_and_repeat_guard(admin_client):
    client, create = admin_client
    good, _ = create()
    changed, payload = create()
    high, _ = create(description="本品治疗失眠，保证根治。")
    uninspected, _ = create()
    products = [good, changed, high, uninspected]
    for product in products[:3]:
        assert client.post(f"/api/admin/products/{product['id']}/inspect", json={"expected_version": 1, "mode": "rules"}).status_code == 200
    candidates = {"items": [{"id": p["id"], "expected_version": 1} for p in products]}
    token = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/admin/products/batch-publish/preview", json=candidates).status_code == 403
    client.headers["X-CSRF-Token"] = token
    preview = client.post("/api/admin/products/batch-publish/preview", json=candidates)
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert (plan["eligible_count"], plan["blocked_count"]) == (2, 2)
    assert "高风险" in plan["items"][2]["error"]
    assert all(client.get(f"/api/admin/products/{p['id']}").json()["status"] == "pending" for p in products)
    assert not any(a["action"] == "publish" for a in plan["items"][0]["product"]["audits"])
    # A concurrent edit after preview must fail without rolling back the other item.
    assert client.put(f"/api/admin/products/{changed['id']}", json={**payload, "title": "新的标题", "expected_version": 1}).status_code == 200
    body = {"items": [item["snapshot"] for item in plan["items"] if item["eligible"]], "reason": "  已逐件核对商品与报告。  "}
    token = client.headers.pop("X-CSRF-Token")
    assert client.post("/api/admin/products/batch-publish", json=body).status_code == 403
    client.headers["X-CSRF-Token"] = token
    response = client.post("/api/admin/products/batch-publish", json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["success_count"], result["failed_count"]) == (1, 1)
    assert result["items"][1]["status_code"] == 409
    stale_preview = client.post("/api/admin/products/batch-publish/preview", json={"items": [{"id": changed["id"], "expected_version": 1}]}).json()
    assert stale_preview["blocked_count"] == 1
    new_preview = client.post("/api/admin/products/batch-publish/preview", json={"items": [{"id": changed["id"]}]}).json()
    assert new_preview["items"][0]["product"]["version"] == 2
    assert not new_preview["items"][0]["eligible"]  # New content must be inspected first.
    published = client.get(f"/api/admin/products/{good['id']}").json()
    audit = [a for a in published["audits"] if a["action"] == "publish"]
    assert len(audit) == 1 and audit[0]["reason"] == body["reason"].strip()
    assert audit[0]["inspection_id"] == body["items"][0]["expected_inspection_id"]
    assert audit[0]["actor"] == "admin-api-test"
    assert client.post("/api/admin/products/batch-publish", json=body).json()["success_count"] == 0
    assert len([a for a in client.get(f"/api/admin/products/{good['id']}").json()["audits"] if a["action"] == "publish"]) == 1


def test_batch_publication_reinspection_status_and_high_risk_guards(admin_client):
    client, create = admin_client
    good, _ = create()
    high, _ = create(description="本品治疗失眠，保证根治。")
    for p in [good, high]:
        client.post(f"/api/admin/products/{p['id']}/inspect", json={"expected_version": 1, "mode": "rules"})
    plan = client.post("/api/admin/products/batch-publish/preview", json={"items": [{"id": good["id"], "expected_version": 1}]}).json()
    body = {"items": [plan["items"][0]["snapshot"]], "reason": "已复核"}
    client.post(f"/api/admin/products/{good['id']}/inspect", json={"expected_version": 1, "mode": "rules"})
    stale = client.post("/api/admin/products/batch-publish", json=body).json()
    assert stale["failed_count"] == 1 and "报告已变化" in stale["items"][0]["error"]
    current = client.get(f"/api/admin/products/{good['id']}").json()
    body["items"][0]["expected_inspection_id"] = current["latest_inspection_id"]
    client.post(f"/api/admin/products/{good['id']}/actions/reject", json={"expected_version": 1, "reason": "待修改"})
    assert "状态已变化" in client.post("/api/admin/products/batch-publish", json=body).json()["items"][0]["error"]
    # A caller can bypass preview but cannot bypass the publication guards.
    current_high = client.get(f"/api/admin/products/{high['id']}").json()
    body["items"] = [{"id": high["id"], "expected_version": 1, "expected_status": "pending", "expected_inspection_id": current_high["latest_inspection_id"]}]
    assert "高风险" in client.post("/api/admin/products/batch-publish", json=body).json()["items"][0]["error"]
    assert client.post("/api/admin/auth/logout").status_code == 200
    assert client.post("/api/admin/products/batch-publish", json=body).status_code == 401
    assert client.post("/api/admin/products/batch-publish/preview", json={"items": [{"id": good["id"], "expected_version": 1}]}).status_code == 401
