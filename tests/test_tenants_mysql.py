"""Opt-in isolated MySQL schemas; cleanup touches only fixture-owned databases."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.skipif(os.getenv("RUN_MYSQL_TESTS") != "1" or not os.getenv("MYSQL_ADMIN_URL"), reason="requires opt-in operator provisioning")


@pytest.fixture
def tenants(tmp_path, monkeypatch):
    from scripts.provision_tenant import provision
    from services.tenancy import tenant_scope
    from db.session import current_engine
    monkeypatch.setenv("TENANTS_FILE", str(tmp_path / "tenants.json"))
    names = ["test-east-" + uuid4().hex[:8], "test-west-" + uuid4().hex[:8]]
    password = "isolated-test-" + uuid4().hex
    created = []
    try:
        for name in names:
            provision(name, "admin", password)
            created.append(name)
        yield names, password
    finally:
        admin = create_engine(os.environ["MYSQL_ADMIN_URL"])
        for name in created:
            with tenant_scope(name):
                current_engine().dispose()
            suffix = hashlib.sha256(name.encode()).hexdigest()[:16]
            with admin.connect() as connection:
                connection.execute(text(f"DROP DATABASE `qa_t_{suffix}`"))
                connection.execute(text(f"DROP USER 'qa_{suffix}'@'%'") )
        admin.dispose()


def test_tenant_apis_cookie_forgery_and_database_grants(tenants):
    from fastapi.testclient import TestClient
    from app.main import app
    from db.session import current_engine
    from services.tenancy import tenant_scope
    from services.simulation import generate_preview
    names, password = tenants
    with TestClient(app) as east, TestClient(app) as west, TestClient(app) as anonymous:
        for client, name in zip((east, west), names):
            login = client.post("/api/admin/auth/login", params={"tenant": name}, json={"username": "admin", "password": password})
            assert login.status_code == 200
            client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
            assert login.json()["tenant"] == name
        payload = generate_preview({"count": 1})["products"][0]["product"]
        a = east.post("/api/admin/products", json=payload).json()
        other = {**payload, "title": "另一租户的私有文案"}
        b = west.post("/api/admin/products", json=other).json()
        assert a["product_id"] == b["product_id"]
        assert east.get(f"/api/admin/products/{a['id']}").json()["title"] == payload["title"]
        assert west.get(f"/api/admin/products/{b['id']}").json()["title"] == other["title"]
        task = east.post(f"/api/admin/products/{a['id']}/inspect", json={"expected_version": 1, "mode": "rules"}).json()["report"]["task_id"]
        for path in (f"/api/results/{task}", f"/api/results/{task}/export", f"/api/tasks/{task}", f"/api/tasks/{task}/traces"):
            assert west.get(path).status_code == 404
            assert anonymous.get(path).status_code == 401
        assert west.get("/api/evaluations/latest").status_code == 404
        assert east.get(f"/api/admin/products/{a['id']}", headers={"X-Tenant-ID": names[1]}).json()["title"] == payload["title"]
        raw = east.cookies.get("admin_session")
        anonymous.cookies.set("admin_session", names[1] + "." + raw.split(".", 1)[1])
        assert anonymous.get("/api/admin/products").status_code == 401
        job = east.post("/api/admin/jobs", json={"items": [{"id": a["id"], "expected_version": 1}], "mode": "rules", "idempotency_key": "same-key"}).json()
        assert west.get(f"/api/admin/jobs/{job['id']}").status_code == 404
        def read(pair):
            client, product = pair
            return client.get(f"/api/admin/products/{product['id']}").json()["title"]
        with ThreadPoolExecutor(2) as pool:
            assert list(pool.map(read, [(east, a), (west, b)])) == [payload["title"], other["title"]]
        for _ in range(5):
            assert west.post("/api/admin/auth/login", params={"tenant": names[1]}, json={"username": "admin", "password": "wrong"}).status_code == 401
            assert east.post("/api/admin/auth/login", params={"tenant": names[0]}, json={"username": "admin", "password": password}).status_code == 200
        assert west.post("/api/admin/auth/login", params={"tenant": names[1]}, json={"username": "admin", "password": "wrong"}).status_code == 429
    with tenant_scope(names[1]):
        other_db = current_engine().url.database
    with tenant_scope(names[0]), current_engine().connect() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(text(f"SELECT COUNT(*) FROM `{other_db}`.managed_products"))


def test_redis_delivery_duplicates_and_rebuild(tenants):
    from services.tenancy import tenant_scope
    from services import broker, jobs, catalog
    from db.session import SessionLocal
    from services.simulation import generate_preview
    from app.catalog_schemas import ProductCreate
    from db.job_models import InspectionJobItem
    with tenant_scope(tenants[0][0]), SessionLocal() as db:
        product = catalog.create_product(db, ProductCreate(**generate_preview({"count": 1})["products"][0]["product"]), "test")
        jobs.enqueue(db, [{"id": product["id"], "expected_version": 1}], "rules", "broker-test", "test")
        key = broker.stream()
        try:
            assert broker.dispatch(db) == 1
            message_id, payload = broker.receive("test-a")
            item_id = int(payload["item_id"])
            assert jobs.claim(db, "test-a", item_id=item_id) == item_id
            with SessionLocal() as peer:
                assert jobs.claim(peer, "test-b", item_id=item_id) is None
            assert jobs.finish_item(db, item_id, ok=False, owner="test-a")
            broker.acknowledge(message_id)
            db.get(InspectionJobItem, item_id).next_attempt_at = jobs._now().replace(year=2000)
            db.commit()
            broker.client().delete(key)  # Only this fixture-owned stream.
            assert broker.dispatch(db) == 1
            second, payload = broker.receive("test-b")
            assert int(payload["item_id"]) == item_id
            assert jobs.claim(db, "test-b", item_id=item_id) == item_id
            assert jobs.finish_item(db, item_id, ok=True, owner="test-a") is False
            assert jobs.finish_item(db, item_id, ok=True, owner="test-b")
            broker.acknowledge(second)
        finally:
            broker.client().delete(key)


def test_failed_tenant_initialization_does_not_block_healthy_tenant(tenants, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    from services.tenancy import tenant_id
    names, password = tenants
    migrate = main.run_migrations
    def unavailable(bind):
        if tenant_id.get() == names[1]:
            raise ConnectionError("test tenant database unavailable")
        return migrate(bind)
    with monkeypatch.context() as patch:
        patch.setattr(main, "run_migrations", unavailable)
        with TestClient(main.app) as client:
            assert client.get("/health").status_code == 200
            good = client.post("/api/admin/auth/login", params={"tenant": names[0]}, json={"username": "admin", "password": password})
            assert good.status_code == 200
            assert client.get("/api/admin/products").status_code == 200
            bad = client.post("/api/admin/auth/login", params={"tenant": names[1]}, json={"username": "admin", "password": password})
            assert bad.status_code == 503
    # A coordinated restart retries initialization after the operator restores DB access.
    with TestClient(main.app) as client:
        assert client.post("/api/admin/auth/login", params={"tenant": names[1]}, json={"username": "admin", "password": password}).status_code == 200


@pytest.mark.parametrize("crash_phase", ["during_execution", "after_catalog_commit"])
def test_worker_recovers_process_exit_without_duplicate_results(tenants, monkeypatch, crash_phase):
    from sqlalchemy import select, update, func
    from services.tenancy import tenant_scope
    from services import jobs, catalog
    from scripts import worker
    from db.session import SessionLocal
    from db.job_models import InspectionJobItem
    from db.catalog_models import ProductInspection
    from db.models import InspectionTask, InspectionResult
    from app.catalog_schemas import ProductCreate
    from services.simulation import generate_preview
    import app.main as main

    class SimulatedProcessExit(BaseException):
        pass

    finish = jobs.finish_item
    def crashing_runner(*args, task_id=None, **kwargs):
        raise SimulatedProcessExit()
    def crashing_finish(*args, **kwargs):
        raise SimulatedProcessExit()

    with tenant_scope(tenants[0][0]), SessionLocal() as db:
        product = catalog.create_product(db, ProductCreate(**generate_preview({"count": 1})["products"][0]["product"]), "test")
        job = jobs.enqueue(db, [{"id": product["id"], "expected_version": 1}], "rules", "worker-crash", "test")
        item_id = db.scalar(select(InspectionJobItem.id).where(InspectionJobItem.job_id == job["id"]))
        with monkeypatch.context() as patch:
            if crash_phase == "during_execution":
                patch.setattr(main, "perform_inspection", crashing_runner)
            else:
                patch.setattr(jobs, "finish_item", crashing_finish)
            with pytest.raises(SimulatedProcessExit):
                worker.execute_once(db, "dead-worker", item_id=item_id)
        db.rollback()
        original_task = db.get(InspectionJobItem, item_id).task_id
        assert db.scalar(select(InspectionTask).where(InspectionTask.task_id == original_task))
        db.execute(update(InspectionJobItem).where(InspectionJobItem.id == item_id)
                   .values(lease_until=jobs._now().replace(year=2000)))
        db.commit()
        assert jobs.recover_expired(db) == 1
        db.get(InspectionJobItem, item_id).next_attempt_at = jobs._now().replace(year=2000)
        db.commit()
        assert not finish(db, item_id, ok=True, owner="dead-worker")
        assert worker.execute_once(db, "replacement-worker", item_id=item_id)
        db.rollback()  # Observe the replacement's separate completion transaction.
        item = db.get(InspectionJobItem, item_id)
        assert item.status == "success"
        attempts = list(db.scalars(select(ProductInspection).where(ProductInspection.managed_product_id == product["id"])))
        assert len(attempts) == (2 if crash_phase == "during_execution" else 1)
        assert (item.task_id != original_task) == (crash_phase == "during_execution")
        assert db.scalar(select(func.count()).select_from(InspectionResult)) == 1
        assert not worker.execute_once(db, "duplicate-delivery", item_id=item_id)


def test_legacy_schema_migration_preserves_rows_and_active_leases():
    from db.migrations import run_migrations
    admin = create_engine(os.environ["MYSQL_ADMIN_URL"])
    schema = "qa_migration_" + uuid4().hex[:16]
    migrated = None
    try:
        with admin.begin() as connection:
            connection.execute(text(f"CREATE DATABASE `{schema}`"))
        migrated = create_engine(admin.url.set(database=schema))
        with migrated.begin() as connection:
            connection.execute(text("CREATE TABLE inspection_tasks (id INT PRIMARY KEY, product_id VARCHAR(128))"))
            connection.execute(text("CREATE TABLE inspection_jobs (id VARCHAR(64) PRIMARY KEY, lease_owner VARCHAR(128), lease_until DATETIME)"))
            connection.execute(text("CREATE TABLE inspection_job_items (id INT PRIMARY KEY, job_id VARCHAR(64), status VARCHAR(16))"))
            connection.execute(text("INSERT INTO inspection_tasks VALUES (1, 'preserved-product')"))
            connection.execute(text("INSERT INTO inspection_jobs VALUES ('old-job', 'old-worker', '2030-01-01 00:00:00')"))
            connection.execute(text("INSERT INTO inspection_job_items VALUES (1, 'old-job', 'running'), (2, 'old-job', 'queued')"))
        assert len(run_migrations(migrated)) == 8
        assert run_migrations(migrated) == []
        with migrated.connect() as connection:
            assert connection.scalar(text("SELECT product_id FROM inspection_tasks")) == "preserved-product"
            rows = connection.execute(text("SELECT lease_owner, lease_until FROM inspection_job_items ORDER BY id")).all()
            assert rows[0][0] == "old-worker" and rows[0][1].year == 2030
            assert rows[1] == (None, None)
            assert connection.scalar(text("SELECT payload_hash FROM inspection_jobs")) == ""
    finally:
        if migrated is not None:
            migrated.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS `{schema}`"))
        admin.dispose()


def test_every_model_http_retry_consumes_rate_budget(monkeypatch):
    import httpx
    from pydantic import BaseModel
    from llm.client import DeepSeekClient, DeepSeekUnavailableError
    from services import broker
    redis_client = broker.client()
    prefix = "qa:test:" + uuid4().hex + ":"
    keys = set()
    class NamespacedRedis:
        def eval(self, script, count, *args):
            names = [prefix + key for key in args[:count]]
            keys.update(names)
            return redis_client.eval(script, count, *names, *args[count:])
        def zrem(self, key, value):
            return redis_client.zrem(prefix + key, value)
    monkeypatch.setattr(broker, "client", lambda: NamespacedRedis())
    monkeypatch.setenv("LLM_DISTRIBUTED_LIMITS", "true")
    monkeypatch.setenv("LLM_REQUESTS_PER_MINUTE", "1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setenv("DEEPSEEK_MAX_RETRIES", "1")
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(500, json={"error": "test"})
    class Result(BaseModel):
        summary: str
    try:
        with httpx.Client(transport=httpx.MockTransport(respond)) as http:
            with pytest.raises(DeepSeekUnavailableError, match="budget"):
                DeepSeekClient(http_client=http).generate_json("summary", {}, Result)
        assert len(calls) == 1
    finally:
        if keys:
            redis_client.delete(*keys)
