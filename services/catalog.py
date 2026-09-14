"""Versioned catalog workflow and publication guards.

An inspection reserves its sequence number in a short transaction, then runs
without a catalog row lock. Completion applies only to that version and the
newest started inspection. Earlier results always remain available in history.
"""
from copy import deepcopy
from datetime import datetime, timezone
import inspect as inspect_module
from uuid import uuid4
from typing import Callable

from sqlalchemy import Integer, and_, cast, event, func, not_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.catalog_schemas import ProductAction, ProductCreate, ProductUpdate
from app.schemas import InspectionReport, ProductInput
from db.catalog_models import ManagedProduct, ProductAudit, ProductInspection, ProductRevision
from db.models import InspectionResult, InspectionTask


class CatalogError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


CONTENT_FIELDS = ("product_id", "merchant_name", "category", "title", "description", "attributes")


def _lock_product(db: Session, product_id: int, version: int | None = None) -> ManagedProduct:
    product = db.scalar(select(ManagedProduct).where(ManagedProduct.id == product_id)
                        .with_for_update().execution_options(populate_existing=True))
    if product is None:
        db.rollback()
        raise CatalogError(404, "商品不存在")
    if version is not None and product.version != version:
        db.rollback()
        raise CatalogError(409, "商品内容已被其他操作修改，请刷新后重试。")
    return product


def _content(product: ManagedProduct) -> dict:
    return deepcopy({field: getattr(product, field) for field in CONTENT_FIELDS})


def _audit(db, product, action, actor, *, previous=None, reason="", inspection_id=None, version=None):
    db.add(ProductAudit(managed_product_id=product.id, action=action,
                        from_status=previous, to_status=product.status,
                        version=product.version if version is None else version, actor=actor, reason=reason,
                        inspection_id=inspection_id))


def _record_revision(db, product, actor):
    db.add(ProductRevision(managed_product_id=product.id, version=product.version,
                           product_json=_content(product), actor=actor))


def _clear_evidence(product):
    product.latest_inspection_id = None
    product.latest_task_id = None
    product.latest_risk = None
    product.inspected_version = None


def create_product(db: Session, data: ProductCreate, actor: str) -> dict:
    data = ProductCreate.model_validate(data)
    product = ManagedProduct(**data.model_dump(), status="pending", version=1)
    try:
        db.add(product)
        db.flush()
        _record_revision(db, product, actor)
        _audit(db, product, "create", actor, reason="创建商品，等待审核。")
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise CatalogError(409, "商品编号已存在，请使用不同的商品编号。") from exc
    return get_product_detail(db, product.id)


def update_product(db: Session, product_id: int, data: ProductUpdate, actor: str) -> dict:
    data = ProductUpdate.model_validate(data)
    product = _lock_product(db, product_id, data.expected_version)
    if product.product_id != data.product_id:
        db.rollback()
        raise CatalogError(422, "商品编号不能修改。")
    payload = data.model_dump(exclude={"expected_version"})
    if _content(product) == payload:
        db.commit()
        return get_product_detail(db, product_id)
    previous = product.status
    for field, value in payload.items():
        setattr(product, field, value)
    product.version += 1
    product.status = "pending"
    _clear_evidence(product)
    _record_revision(db, product, actor)
    _audit(db, product, "edit", actor, previous=previous,
           reason="商品内容已修改，历史质检失效，需检查新版本。")
    db.commit()
    return get_product_detail(db, product_id)


def _fresh(product, inspection, task) -> bool:
    return bool(inspection and task and inspection.id == product.latest_inspection_id
                and inspection.version == product.version == product.inspected_version
                and inspection.task_id == task.task_id == product.latest_task_id
                and task.product_id == product.product_id
                and inspection.status == task.status == "success")


def _complete(inspection) -> bool:
    report = inspection.report_json if inspection else None
    return bool(report and inspection.status == report.get("status") == "success"
                and report.get("rules") and report.get("retrieval_source") != "unavailable"
                and report.get("mode") == inspection.mode
                and (inspection.mode == "rules" or (not report.get("degraded") and report.get("model_used"))))


def _serialize(product, inspection=None, task=None):
    result = {field: getattr(product, field) for field in (
        "id", *CONTENT_FIELDS, "status", "version", "latest_inspection_id", "latest_task_id", "latest_risk",
        "inspected_version", "created_at", "updated_at")}
    result["inspection_fresh"] = _fresh(product, inspection, task)
    result["inspection_complete"] = result["inspection_fresh"] and _complete(inspection)
    current = inspection and inspection.version == product.version
    result["inspection_status"] = inspection.status if current else None
    result["issue_count"] = inspection.issue_count if current else 0
    result["risk_level"] = product.latest_risk
    return result


def _inspection_dict(inspection):
    return {field: getattr(inspection, field) for field in (
        "id", "version", "task_id", "mode", "status", "risk_level", "issue_count",
        "degraded", "error_message", "actor", "created_at", "finished_at")}


def _query():
    return select(ManagedProduct, ProductInspection, InspectionTask).outerjoin(
        ProductInspection, ProductInspection.id == ManagedProduct.latest_inspection_id).outerjoin(
        InspectionTask, InspectionTask.task_id == ManagedProduct.latest_task_id)


def _fresh_clause():
    return and_(ProductInspection.id == ManagedProduct.latest_inspection_id,
                ProductInspection.version == ManagedProduct.version,
                ManagedProduct.inspected_version == ManagedProduct.version,
                ProductInspection.task_id == ManagedProduct.latest_task_id,
                InspectionTask.product_id == ManagedProduct.product_id,
                ProductInspection.status == "success", InspectionTask.status == "success")


def _complete_clause():
    """SQL equivalent of _complete; JSON predicates keep list/summary aligned."""
    return and_(_fresh_clause(), ProductInspection.report_json.is_not(None),
                func.json_length(func.json_extract(ProductInspection.report_json, "$.rules")) > 0,
                func.json_extract(ProductInspection.report_json, "$.retrieval_source") != "\"unavailable\"",
                or_(ProductInspection.mode == "rules",
                    and_(ProductInspection.degraded.is_(False),
                         func.json_extract(ProductInspection.report_json, "$.model_used") == "true")))


def _filters(q=None, category=None, status=None, risk=None):
    clauses = []
    if q and q.strip():
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append(or_(*(field.ilike(f"%{term}%", escape="\\") for field in (
            ManagedProduct.product_id, ManagedProduct.merchant_name, ManagedProduct.title))))
    if category:
        clauses.append(ManagedProduct.category == category)
    if status:
        clauses.append(ManagedProduct.status == status)
    complete = _complete_clause()
    if risk == "uninspected":
        clauses.append(not_(func.coalesce(complete, False)))
    elif risk:
        clauses.extend([complete,
                        ManagedProduct.latest_risk == risk])
    return clauses


def list_products(db: Session, *, q=None, category=None, status=None, risk=None, page=1, page_size=20) -> dict:
    if page < 1 or not 1 <= page_size <= 100:
        raise CatalogError(422, "分页参数不合法。")
    query = _query().where(*_filters(q, category, status, risk))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(query.order_by(ManagedProduct.updated_at.desc(), ManagedProduct.id.desc())
                      .offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [_serialize(*row) for row in rows], "total": total, "page": page, "page_size": page_size}


def get_product_detail(db: Session, product_id: int) -> dict:
    row = db.execute(_query().where(ManagedProduct.id == product_id)
                     .execution_options(populate_existing=True)).first()
    if row is None:
        raise CatalogError(404, "商品不存在")
    product, inspection, _ = row
    result = _serialize(*row)
    result["latest_report"] = inspection.report_json if inspection and inspection.version == product.version else None
    result.update(_history(db, product_id, "revisions", None, 20))
    result.update(_history(db, product_id, "inspections", None, 20))
    result.update(_history(db, product_id, "audits", None, 20))
    return result


def _history(db: Session, product_id: int, kind: str, before: int | None, limit: int = 20) -> dict:
    limit = max(1, min(int(limit), 100))
    if kind == "revisions":
        stmt = select(ProductRevision).where(ProductRevision.managed_product_id == product_id)
        if before is not None: stmt = stmt.where(ProductRevision.version < before)
        rows = db.scalars(stmt.order_by(ProductRevision.version.desc()).limit(limit + 1)).all()
        items = [{"version": x.version, "product_json": x.product_json, "actor": x.actor, "created_at": x.created_at} for x in rows[:limit]]
        nxt = rows[limit - 1].version if len(rows) > limit else None
    elif kind == "inspections":
        stmt = select(ProductInspection).where(ProductInspection.managed_product_id == product_id)
        if before is not None: stmt = stmt.where(ProductInspection.id < before)
        rows = db.scalars(stmt.order_by(ProductInspection.id.desc()).limit(limit + 1)).all()
        items = [_inspection_dict(x) for x in rows[:limit]]
        nxt = rows[limit - 1].id if len(rows) > limit else None
    elif kind == "audits":
        stmt = select(ProductAudit).where(ProductAudit.managed_product_id == product_id)
        if before is not None: stmt = stmt.where(ProductAudit.id < before)
        rows = db.scalars(stmt.order_by(ProductAudit.id.desc()).limit(limit + 1)).all()
        items = [{field: getattr(x, field) for field in ("id", "action", "from_status", "to_status", "version", "actor", "reason", "inspection_id", "created_at")} for x in rows[:limit]]
        nxt = rows[limit - 1].id if len(rows) > limit else None
    else:
        raise CatalogError(422, "历史类型必须是 revisions、inspections 或 audits。")
    return {kind: items, f"{kind}_next": nxt}


def get_product_history(db: Session, product_id: int, kind: str, before: int | None = None, limit: int = 20) -> dict:
    if db.scalar(select(ManagedProduct.id).where(ManagedProduct.id == product_id)) is None:
        raise CatalogError(404, "商品不存在")
    return _history(db, product_id, kind, before, limit)


def get_summary(db: Session) -> dict:
    # Aggregate the same current-version evidence used by the list and publish guard.
    counts = db.execute(select(
        func.count(ManagedProduct.id),
        func.sum(cast(ManagedProduct.status == "published", Integer)),
        func.sum(cast(ManagedProduct.status == "pending", Integer)),
        func.sum(cast(and_(ManagedProduct.inspected_version == ManagedProduct.version, ManagedProduct.latest_risk == "high"), Integer)),
        func.sum(cast(not_(func.coalesce(_complete_clause(), False)), Integer)),
        func.count(func.distinct(ManagedProduct.merchant_name)),
    ).select_from(ManagedProduct).outerjoin(ProductInspection, ProductInspection.id == ManagedProduct.latest_inspection_id)
       .outerjoin(InspectionTask, InspectionTask.task_id == ManagedProduct.latest_task_id)).one()
    return dict(zip(("total", "published", "pending", "high_risk", "uninspected", "merchants"),
                    (int(value or 0) for value in counts)))


def _publication_evidence(db, product, expected_inspection_id):
    # Current reads share the product lock with edits, reinspections and publishing.
    inspection = db.get(ProductInspection, product.latest_inspection_id, populate_existing=True,
                        with_for_update=True) if product.latest_inspection_id else None
    if not inspection:
        raise CatalogError(409, "商品没有成功且完整的当前质检报告，不能发布。")
    if expected_inspection_id is None or inspection.id != expected_inspection_id:
        raise CatalogError(409, "质检报告已变化，请重新打开报告并确认后发布。")
    task = db.scalar(select(InspectionTask).where(InspectionTask.task_id == product.latest_task_id)
                     .with_for_update().execution_options(populate_existing=True)) if product.latest_task_id else None
    if not _fresh(product, inspection, task) or not _complete(inspection):
        raise CatalogError(409, "当前版本没有成功且完整的质检结果，请先完成质检；完整模式降级时可重新运行规则质检。")
    stored = db.scalar(select(InspectionResult).where(InspectionResult.task_id == product.latest_task_id)
                       .with_for_update().execution_options(populate_existing=True))
    if stored is None or stored.report_json != inspection.report_json:
        raise CatalogError(409, "质检原始记录缺失或不一致，请重新质检。")
    report = inspection.report_json
    if report.get("category") != product.category:
        raise CatalogError(409, "质检类目与商品不一致，请重新质检。")
    if inspection.risk_level == "high" or any(issue.get("risk_level") == "high" for issue in report.get("issues", [])):
        raise CatalogError(409, "高风险商品不能发布，请修改内容并重新质检。")
    return inspection


def preview_publication(db: Session, product_id: int, expected_version: int | None) -> dict:
    product = _lock_product(db, product_id, expected_version)
    try:
        detail = get_product_detail(db, product_id)
        error = None
        try:
            if product.status not in {"pending", "offline"}:
                raise CatalogError(409, "当前商品状态不支持此操作，请刷新后重试。")
            _publication_evidence(db, product, product.latest_inspection_id)
        except CatalogError as exc:
            error = exc.detail
        snapshot = {"id": product.id, "expected_version": product.version,
                    "expected_status": product.status, "expected_inspection_id": product.latest_inspection_id}
        return {"id": product.id, "eligible": error is None, "product": detail,
                "snapshot": snapshot if error is None else None, "error": error}
    finally:
        # Preview is read-only and never holds locks while an administrator reviews.
        db.rollback()


def change_status(db: Session, product_id: int, action: str, data: ProductAction, actor: str) -> dict:
    data = ProductAction.model_validate(data)
    product = _lock_product(db, product_id, data.expected_version)
    try:
        if data.expected_status and data.expected_status != product.status:
            raise CatalogError(409, "商品状态已变化，请刷新后重试。")
        transitions = {"publish": ({"pending", "offline"}, "published"),
                       "offline": ({"published"}, "offline"),
                       "reject": ({"pending"}, "rejected"),
                       "submit": ({"draft", "rejected", "offline"}, "pending")}
        if action not in transitions:
            raise CatalogError(422, "不支持的商品操作。")
        allowed, next_status = transitions[action]
        if product.status not in allowed:
            raise CatalogError(409, "当前商品状态不支持此操作，请刷新后重试。")
        if action == "reject" and not data.reason:
            raise CatalogError(422, "退回商品必须填写原因。")
        if action == "publish":
            inspection = _publication_evidence(db, product, data.expected_inspection_id)
            if inspection.risk_level in {"low", "medium"} and not data.reason:
                raise CatalogError(422, "中低风险商品发布前必须填写人工审核说明。")
        previous = product.status
        product.status = next_status
        _audit(db, product, action, actor, previous=previous, reason=data.reason,
               inspection_id=product.latest_inspection_id)
        db.commit()
    except CatalogError:
        db.rollback()
        raise
    return get_product_detail(db, product_id)


def inspect_product(db: Session, product_id: int, expected_version: int, mode: str, actor: str,
                    runner: Callable[..., InspectionReport], task_id: str | None = None) -> dict:
    if mode not in {"rules", "full"}:
        raise CatalogError(422, "质检模式必须是 rules 或 full。")
    product = _lock_product(db, product_id, expected_version)
    payload = _content(product)
    inspection = ProductInspection(managed_product_id=product_id, version=product.version,
                                   mode=mode, status="running", actor=actor)
    db.add(inspection)
    db.flush()
    inspection_id = inspection.id
    # New application runners accept a durable task id. Keep old injected
    # runners source compatible for tests and integrations while avoiding an
    # orphan running task for them.
    runner_parameters = inspect_module.signature(runner).parameters
    runner_accepts_task = "task_id" in runner_parameters or any(
        parameter.kind == inspect_module.Parameter.VAR_KEYWORD
        for parameter in runner_parameters.values()
    )
    durable_task_id = (task_id or f"task_{uuid4().hex}") if runner_accepts_task else None
    if durable_task_id:
        db.add(InspectionTask(task_id=durable_task_id, product_id=payload["product_id"],
                              status="running", trigger_source="admin"))
        inspection.task_id = durable_task_id
    _clear_evidence(product)
    product.latest_inspection_id = inspection_id
    if product.status == "published":
        product.status = "pending"
        _audit(db, product, "inspection_recalled", actor, previous="published",
               reason="开始重新质检，旧发布依据已失效，暂停发布；新结果需重新审核发布。",
               inspection_id=inspection_id)
    _audit(db, product, "inspect_start", actor, previous=product.status,
           reason=f"开始检查第 {expected_version} 版（{mode}）。", inspection_id=inspection_id)
    db.commit()  # Never hold this product lock during retrieval or model requests.
    error = None
    report = None
    try:
        normalized = ProductInput.model_validate({key: value for key, value in payload.items() if key != "merchant_name"})
        if durable_task_id and "task_id" in inspect_module.signature(runner).parameters:
            report = InspectionReport.model_validate(
                runner(normalized, db, mode, trigger_source="admin", task_id=durable_task_id))
        else:
            report = InspectionReport.model_validate(runner(normalized, db, mode, trigger_source="admin"))
    except Exception as exc:
        db.rollback()
        error = exc
    # The runner can commit and its transaction may contain an older snapshot.
    db.rollback()
    # Compatibility for injected legacy runners: they may create and persist
    # their own task before raising. The application runner always receives the
    # durable id above, but retaining this lookup keeps failure history linked
    # for integrations that still use the old callback contract.
    legacy_task = None
    if error is not None and durable_task_id is None:
        legacy_task = db.scalar(select(InspectionTask).where(
            InspectionTask.product_id == payload["product_id"],
            InspectionTask.trigger_source == "admin",
        ).order_by(InspectionTask.id.desc()))
    product = _lock_product(db, product_id)
    inspection = db.get(ProductInspection, inspection_id, populate_existing=True)
    inspection.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if report is not None:
        # The task identity is reserved before execution; reports cannot
        # replace it (legacy runners may return a generated id).
        inspection.task_id = durable_task_id or report.task_id
        inspection.status = report.status
        inspection.risk_level = report.risk_level
        inspection.issue_count = len(report.issues)
        inspection.degraded = report.degraded
        inspection.report_json = report.model_dump()
    else:
        inspection.task_id = durable_task_id or (legacy_task.task_id if legacy_task else None)
        inspection.status = "failed"
        inspection.error_message = f"{type(error).__name__}: 质检未完成，请重试或检查服务状态。"
    applied = product.version == expected_version and product.latest_inspection_id == inspection_id
    if applied:
        product.latest_task_id = inspection.task_id
        product.latest_risk = inspection.risk_level
        product.inspected_version = expected_version
        review_required = inspection.risk_level != "pass" or (report is not None and bool(report.issues))
        if product.status == "published" and (review_required or not _complete(inspection)):
            previous = product.status
            product.status = "pending"
            _audit(db, product, "inspection_recalled", actor, previous=previous,
                   reason="重检发现风险或质检不完整，已撤回发布并转为待审核；新报告需要重新审核。", inspection_id=inspection_id)
    _audit(db, product, "inspect_complete" if report is not None else "inspect_failed", actor,
           previous=product.status, reason=("结果已应用于当前版本。" if applied else "仅归档历史结果，未覆盖当前版本。"),
           inspection_id=inspection_id, version=expected_version)
    db.commit()
    if error is not None:
        raise CatalogError(502, "质检执行失败，失败记录已保存，请重试或检查服务状态。") from error
    return {"product": get_product_detail(db, product_id), "inspection": _inspection_dict(inspection),
            "report": report.model_dump(), "applied": applied}
