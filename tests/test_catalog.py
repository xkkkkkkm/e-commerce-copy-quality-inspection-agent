"""Catalog workflow checks. MySQL cases opt in with RUN_MYSQL_TESTS=1.

Each database case uses a unique product and removes only its own records.
Threaded tests exercise InnoDB locks, not SQLite's different lock semantics.
"""
from concurrent.futures import ThreadPoolExecutor, wait
import os
from threading import Event
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.catalog_schemas import ProductAction, ProductCreate, ProductInspect, ProductUpdate
from app.schemas import InspectionReport, Issue


def body(**changes):
    data = {"product_id": "catalog_test_" + uuid4().hex, "merchant_name": "测试专用店铺",
            "category": "食品", "title": "燕麦早餐谷物 500g", "description": "燕麦与混合坚果制成的即食谷物。",
            "attributes": {"brand": "谷物集", "origin": "山东", "shelf_life": "12个月",
                           "ingredients": "燕麦、杏仁", "storage": "阴凉干燥处"}}
    data.update(changes)
    return data


@pytest.fixture
def store():
    if os.getenv("RUN_MYSQL_TESTS") != "1":
        pytest.skip("requires project MySQL; set RUN_MYSQL_TESTS=1")
    from sqlalchemy import delete, select
    from db.catalog_models import CATALOG_TABLES, ManagedProduct, ProductAudit, ProductInspection, ProductRevision
    from db.models import AgentTrace, InspectionResult, InspectionTask
    from db.session import Base, SessionLocal, engine
    from services.catalog import create_product

    Base.metadata.create_all(bind=engine, tables=CATALOG_TABLES)
    created = []
    with SessionLocal() as db:
        def create(**changes):
            data = ProductCreate(**body(**changes))
            product = create_product(db, data, "test:creator")
            created.append((product["id"], product["product_id"]))
            return product
        yield db, create
    with SessionLocal() as cleanup:
        for managed_id, product_id in created:
            task_ids = cleanup.scalars(select(InspectionTask.task_id).where(InspectionTask.product_id == product_id)).all()
            if task_ids:
                cleanup.execute(delete(AgentTrace).where(AgentTrace.task_id.in_(task_ids)))
                cleanup.execute(delete(InspectionResult).where(InspectionResult.task_id.in_(task_ids)))
                cleanup.execute(delete(InspectionTask).where(InspectionTask.task_id.in_(task_ids)))
            for model in (ProductAudit, ProductInspection, ProductRevision):
                cleanup.execute(delete(model).where(model.managed_product_id == managed_id))
            cleanup.execute(delete(ManagedProduct).where(ManagedProduct.id == managed_id))
        cleanup.commit()


def runner(*, risk="pass", status="success", degraded=False, model_used=False, rules=True,
           started=None, release=None, failure=False):
    from db.repositories import create_task, save_failure, save_success

    def run(product, db, mode, trigger_source="api"):
        task = create_task(db, product, trigger_source)
        if started:
            started.set()
        if release:
            assert release.wait(10), "test runner was not released"
        if failure:
            exc = RuntimeError("controlled failure")
            save_failure(db, task, exc)
            raise exc
        issues = [] if risk == "pass" else [Issue(issue_type="测试问题", field="description", risk_level=risk,
                                                   evidence="测试证据", suggestion="测试建议", rule_id="test_rule")]
        report = InspectionReport(task_id=task.task_id, status=status, risk_level=risk, score=0 if risk == "pass" else 20,
                                  issues=issues, optimized_title=product.title, optimized_description=product.description,
                                  category=product.category, mode=mode, degraded=degraded, model_used=model_used,
                                  rules=[{"rule_id": "test_rule", "category": product.category}] if rules else [])
        save_success(db, task, report)
        return report
    return run


def inspect(db, product, **runner_options):
    from services.catalog import inspect_product
    return inspect_product(db, product["id"], product["version"], runner_options.pop("mode", "rules"),
                           "test:inspector", runner(**runner_options))


def action(db, product, name="publish", reason="", **kwargs):
    from services.catalog import change_status
    if name == "publish" and product.get("latest_inspection_id") is not None:
        kwargs.setdefault("expected_inspection_id", product["latest_inspection_id"])
    return change_status(db, product["id"], name,
                         ProductAction(expected_version=product["version"], reason=reason, **kwargs), "test:reviewer")


def edit(db, product, **changes):
    from services.catalog import update_product
    payload = {key: product[key] for key in ("product_id", "merchant_name", "category", "title", "description", "attributes")}
    payload.update(changes)
    return update_product(db, product["id"], ProductUpdate(**payload, expected_version=product["version"]), "test:editor")


def test_admin_inputs_reject_unknown_fields_and_utf8_overflow():
    with pytest.raises(ValidationError):
        ProductCreate(**body(status="published"))
    with pytest.raises(ValidationError):
        ProductCreate(**body(description="😀" * 20000))
    with pytest.raises(ValidationError):
        ProductCreate(**body(merchant_name="   "))
    with pytest.raises(ValidationError):
        ProductInspect(expected_version=True)
    with pytest.raises(ValidationError):
        ProductAction(expected_version=1, approve=True)


@pytest.mark.parametrize("changes", [
    {"reason": "   "}, {"reason": "x" * 2001}, {"items": []},
    {"items": [{"id": 1, "expected_version": 1}]},
    {"items": [{"id": True, "expected_version": 1, "expected_status": "pending", "expected_inspection_id": 1}]},
    {"items": [{"id": 1, "expected_version": 1, "expected_status": "published", "expected_inspection_id": 1}]},
    {"force": True},
])
def test_batch_publication_strict_inputs(changes):
    from app.catalog_schemas import BatchPublication
    payload = {"items": [{"id": 1, "expected_version": 1, "expected_status": "pending", "expected_inspection_id": 1}], "reason": "Reviewed"}
    with pytest.raises(ValidationError):
        BatchPublication.model_validate({**payload, **changes})


def test_batch_publication_limits_and_duplicates():
    from app.catalog_schemas import BatchPublication, BatchPublicationPreview
    for model in [BatchPublication, BatchPublicationPreview]:
        item = {"id": 1, "expected_version": 1}
        extra = {}
        if model is BatchPublication:
            item.update(expected_status="pending", expected_inspection_id=1)
            extra["reason"] = "reviewed"
        for items in [[item, item], [{**item, "id": i + 1} for i in range(21)]]:
            with pytest.raises(ValidationError):
                model.model_validate({"items": items, **extra})


@pytest.mark.parametrize("options", [
    {"risk": "high"}, {"status": "partial"}, {"status": "failed"}, {"rules": False},
    {"mode": "full", "degraded": True, "model_used": True}, {"mode": "full", "model_used": False},
])
def test_publication_preview_uses_full_evidence_guard(store, options):
    from services.catalog import preview_publication
    db, create = store
    product = inspect(db, create(), **options)["product"]
    preview = preview_publication(db, product["id"], 1)
    assert not preview["eligible"] and preview["error"] and preview["snapshot"] is None
    assert preview["product"]["status"] == "pending"


def test_concurrent_publication_of_same_snapshot_has_one_audit(store):
    from services.catalog import CatalogError, change_status, get_product_detail, preview_publication
    from db.session import SessionLocal
    db, create = store
    product = inspect(db, create())["product"]
    plan = preview_publication(db, product["id"], 1)
    payload = ProductAction(**{k: v for k, v in plan["snapshot"].items() if k != "id"}, reason="Batch review")
    def publish():
        with SessionLocal() as session:
            try:
                change_status(session, product["id"], "publish", payload, "concurrent-reviewer")
                return True
            except CatalogError as exc:
                assert exc.status_code == 409
                return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: publish(), range(2))) == [False, True]
    latest = get_product_detail(db, product["id"])
    assert len([a for a in latest["audits"] if a["action"] == "publish"]) == 1


def test_publication_requires_current_report_and_creates_audit(store):
    from services.catalog import CatalogError
    db, create = store
    product = create()
    with pytest.raises(CatalogError, match="没有成功且完整"):
        action(db, product)
    product = inspect(db, product)["product"]
    published = action(db, product)
    assert published["status"] == "published"
    assert published["inspection_fresh"] and published["inspection_complete"]
    assert published["audits"][0]["action"] == "publish"
    assert published["audits"][0]["actor"] == "test:reviewer"


@pytest.mark.parametrize("options", [
    {"risk": "high"}, {"status": "partial"}, {"status": "failed"}, {"rules": False},
    {"mode": "full", "degraded": True, "model_used": True}, {"mode": "full", "model_used": False},
])
def test_ineligible_reports_cannot_publish(store, options):
    from services.catalog import CatalogError
    db, create = store
    product = inspect(db, create(), **options)["product"]
    with pytest.raises(CatalogError):
        action(db, product, reason="不能绕过发布门槛")
    assert product["status"] == "pending"


@pytest.mark.parametrize("risk", ["low", "medium"])
def test_low_medium_publication_requires_human_review_note(store, risk):
    from services.catalog import CatalogError
    db, create = store
    product = inspect(db, create(), risk=risk)["product"]
    with pytest.raises(CatalogError, match="人工审核说明"):
        action(db, product, reason="   ")
    product = action(db, product, reason="已核对商品资质和文案，确认允许发布。")
    assert product["status"] == "published"
    assert product["audits"][0]["reason"] == "已核对商品资质和文案，确认允许发布。"


def test_edit_invalidates_evidence_preserves_exact_revision_and_rejects_stale_write(store):
    from services.catalog import CatalogError
    db, create = store
    product = create()
    original_title = product["title"]
    checked = inspect(db, product)["product"]
    published = action(db, checked)
    updated = edit(db, published, title="修改后的燕麦早餐谷物 500g")
    assert updated["version"] == 2 and updated["status"] == "pending"
    assert updated["latest_task_id"] is None and updated["latest_report"] is None
    assert not updated["inspection_fresh"]
    assert [r["version"] for r in updated["revisions"]] == [2, 1]
    assert updated["revisions"][1]["product_json"]["title"] == original_title
    assert updated["inspections"][0]["version"] == 1
    with pytest.raises(CatalogError, match="内容已被"):
        edit(db, product, description="过期写入")
    with pytest.raises(CatalogError):
        action(db, updated)
    assert edit(db, updated)["version"] == 2  # Saving identical content does not create a fake revision.


def test_failed_inspection_keeps_task_link_and_retracts_published_product(store):
    from services.catalog import CatalogError, get_product_detail
    db, create = store
    product = action(db, inspect(db, create())["product"])
    with pytest.raises(CatalogError, match="失败记录已保存"):
        inspect(db, product, failure=True)
    failed = get_product_detail(db, product["id"])
    assert failed["status"] == "pending"
    assert failed["inspections"][0]["status"] == "failed"
    assert failed["inspections"][0]["task_id"]
    assert not failed["inspection_fresh"]
    assert any(a["action"] == "inspection_recalled" for a in failed["audits"])


@pytest.mark.parametrize("options", [
    {"risk": "high"}, {"risk": "low"}, {"risk": "medium"}, {"status": "partial"},
    {"mode": "full", "degraded": True, "model_used": True},
])
def test_published_reinspection_cannot_leave_unsafe_product_published(store, options):
    from services.catalog import CatalogError
    db, create = store
    product = action(db, inspect(db, create())["product"])
    result = inspect(db, product, **options)
    assert result["product"]["status"] == "pending"
    assert any(a["action"] == "inspection_recalled" for a in result["product"]["audits"])
    if options.get("risk") in {"low", "medium"}:
        with pytest.raises(CatalogError, match="人工审核说明"):
            action(db, result["product"])
        assert action(db, result["product"], reason="对新报告重新核对，确认允许发布。")["status"] == "published"


def test_reinspection_pauses_published_product_before_runner_and_requires_republish(store):
    from db.session import SessionLocal
    from services.catalog import get_product_detail, inspect_product
    db, create = store
    product = action(db, inspect(db, create())["product"])
    started, release = Event(), Event()

    def recheck():
        with SessionLocal() as session:
            return inspect_product(session, product["id"], 1, "rules", "test:recheck", runner(started=started, release=release))

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(recheck)
        assert started.wait(5)
        try:
            db.rollback()
            running = get_product_detail(db, product["id"])
            assert running["status"] == "pending" and running["inspection_status"] == "running"
            assert not running["inspection_fresh"] and running["latest_task_id"] is None
        finally:
            release.set()
        completed = pending.result(timeout=5)["product"]
    assert completed["status"] == "pending" and completed["latest_risk"] == "pass"
    assert action(db, completed)["status"] == "published"


def test_only_latest_started_same_version_inspection_can_apply(store):
    from db.session import SessionLocal
    from services.catalog import get_product_detail, inspect_product
    db, create = store
    product = create()
    started, release = Event(), Event()

    def old_inspection():
        with SessionLocal() as session:
            return inspect_product(session, product["id"], 1, "rules", "test:slow", runner(risk="high", started=started, release=release))

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(old_inspection)
        assert started.wait(5)
        try:
            current = inspect(db, product)["product"]
            assert current["latest_risk"] == "pass"
            newest_task = current["latest_task_id"]
        finally:
            release.set()
        assert pending.result(timeout=5)["applied"] is False
    db.rollback()
    detail = get_product_detail(db, product["id"])
    assert detail["latest_task_id"] == newest_task and detail["latest_risk"] == "pass"
    assert len(detail["inspections"]) == 2
    assert detail["inspections"][1]["risk_level"] == "high"


def test_edit_during_inspection_is_unblocked_and_old_result_only_archived(store):
    from db.session import SessionLocal
    from services.catalog import get_product_detail, inspect_product
    db, create = store
    product = create()
    started, release = Event(), Event()

    def slow_inspection():
        with SessionLocal() as session:
            return inspect_product(session, product["id"], 1, "rules", "test:slow", runner(started=started, release=release))

    def concurrent_edit():
        with SessionLocal() as session:
            return edit(session, product, description="检查期间修改后的真实文案")

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(slow_inspection)
        assert started.wait(5)
        try:
            updated = pool.submit(concurrent_edit).result(timeout=3)
            assert updated["version"] == 2
        finally:
            release.set()
        result = pending.result(timeout=5)
    assert result["applied"] is False
    db.rollback()
    detail = get_product_detail(db, product["id"])
    assert detail["latest_task_id"] is None and detail["version"] == 2
    assert detail["inspections"][0]["version"] == 1
    completion = next(a for a in detail["audits"] if a["action"] == "inspect_complete")
    assert completion["version"] == 1


def test_mysql_row_lock_serializes_edit_and_rejects_lost_update(store):
    from sqlalchemy import select
    from db.catalog_models import ManagedProduct
    from db.session import SessionLocal
    from services.catalog import CatalogError, get_product_detail
    db, create = store
    product = create()
    started = Event()
    db.scalar(select(ManagedProduct).where(ManagedProduct.id == product["id"]).with_for_update())

    def competing_write():
        with SessionLocal() as session:
            started.set()
            try:
                edit(session, product, title="第二个操作者的覆盖写入")
            except CatalogError as exc:
                return exc.status_code
            return 200

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(competing_write)
        assert started.wait(5)
        assert not wait([future], timeout=0.15).done
        updated = edit(db, product, title="第一个操作者保存的标题")
        assert future.result(timeout=5) == 409
    db.rollback()
    detail = get_product_detail(db, product["id"])
    assert detail["title"] == updated["title"] and detail["version"] == 2
    assert len(detail["revisions"]) == 2


def test_state_transitions_and_status_guard(store):
    from services.catalog import CatalogError
    db, create = store
    product = create()
    with pytest.raises(CatalogError):
        action(db, product, "offline")
    with pytest.raises(CatalogError, match="必须填写原因"):
        action(db, product, "reject")
    rejected = action(db, product, "reject", "文案需要重写")
    with pytest.raises(CatalogError, match="状态已变化"):
        action(db, rejected, "submit", expected_status="pending")
    submitted = action(db, rejected, "submit", expected_status="rejected")
    assert submitted["status"] == "pending"
    published = action(db, inspect(db, submitted)["product"])
    assert action(db, published, "offline")["status"] == "offline"


def test_filter_current_version_and_escape_search_wildcards(store):
    from services.catalog import get_summary, list_products
    db, create = store
    marker = "catalog_test_" + uuid4().hex
    first = create(product_id=marker + "_a", merchant_name="百分比%商家")
    second = create(product_id=marker + "_b", merchant_name="普通商家")
    inspect(db, first, risk="high")
    assert list_products(db, q=marker, risk="high")["total"] == 1
    assert list_products(db, q=marker, risk="uninspected")["total"] == 1
    assert any(p["id"] == first["id"] for p in list_products(db, q="百分比%商家")["items"])
    edit(db, first, description="已经修改旧高风险内容")
    assert list_products(db, q=marker, risk="high")["total"] == 0
    assert list_products(db, q=marker, risk="uninspected")["total"] == 2
    paginated = list_products(db, q=marker, page_size=1, page=2)
    assert paginated["total"] == 2 and len(paginated["items"]) == 1
    summary = get_summary(db)
    assert summary["total"] >= 2 and summary["uninspected"] >= 2


def test_missing_or_failed_original_task_does_not_authorize_publication(store):
    from sqlalchemy import select
    from db.models import InspectionTask
    from services.catalog import CatalogError
    db, create = store
    product = inspect(db, create())["product"]
    task = db.scalar(select(InspectionTask).where(InspectionTask.task_id == product["latest_task_id"]))
    task.status = "failed"
    db.commit()
    with pytest.raises(CatalogError, match="没有成功且完整"):
        action(db, product)
