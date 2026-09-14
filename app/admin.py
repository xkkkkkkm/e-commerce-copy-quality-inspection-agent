"""Administrator routes. Catalog operations require a live session and CSRF."""
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from app.admin_auth import require_admin
from app.catalog_schemas import (BatchPublication, BatchPublicationPreview,
                                 ProductAction, ProductCreate, ProductInspect, ProductUpdate)
from db.session import SessionLocal, get_db
from services import catalog
from services import jobs


router = APIRouter(prefix="/api/admin", tags=["管理后台"], dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)


class BatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int = Field(gt=0)
    expected_version: int = Field(gt=0)


class BatchInspection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[BatchItem] = Field(min_length=1, max_length=20)
    mode: Literal["rules", "full"] = "rules"

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("批量质检不能包含重复商品")
        return self


class JobRequest(BatchInspection):
    idempotency_key: str = Field(min_length=1, max_length=200)


def call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except catalog.CatalogError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def inspect_one(db, product_id, expected_version, mode, actor):
    # Import here to avoid a cycle with the application's route registration.
    from app.main import perform_inspection

    return call(catalog.inspect_product, db, product_id, expected_version, mode, actor, perform_inspection)


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    return catalog.get_summary(db)


@router.get("/products")
def products(q: str | None = Query(default=None, max_length=200),
             category: Literal["食品", "美妆", "3C"] | None = None,
             status: Literal["draft", "pending", "published", "rejected", "offline"] | None = None,
             risk: Literal["uninspected", "pass", "low", "medium", "high"] | None = None,
             page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
             db: Session = Depends(get_db)):
    return call(catalog.list_products, db, q=q, category=category, status=status, risk=risk,
                page=page, page_size=page_size)


@router.post("/products", status_code=201)
def create(data: ProductCreate, actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    return call(catalog.create_product, db, data, actor)


@router.post("/products/batch-inspect")
def batch_inspect(data: BatchInspection, actor: str = Depends(require_admin)):
    results = []
    for item in data.items:
        # Isolate each item so a failed task cannot poison the next transaction.
        with SessionLocal() as db:
            try:
                result = inspect_one(db, item.id, item.expected_version, data.mode, actor)
                ok = bool(result.get("report") and result["report"].get("status") == "success"
                          and result.get("applied") and result.get("product", {}).get("inspection_complete"))
                results.append({"id": item.id, "ok": ok, "result": result,
                                "error": None if ok else "质检未完成或商品版本已变化，请查看记录。"})
            except HTTPException as exc:
                db.rollback()
                results.append({"id": item.id, "ok": False, "error": exc.detail})
            except Exception:
                db.rollback()
                results.append({"id": item.id, "ok": False, "error": "处理失败，请检查服务状态。"})
    success = sum(item["ok"] for item in results)
    return {"items": results, "success_count": success, "failed_count": len(results)-success}


@router.post("/products/batch-publish/preview")
def preview_batch_publish(data: BatchPublicationPreview, actor: str = Depends(require_admin)):
    items = []
    for item in data.items:
        with SessionLocal() as db:
            try:
                items.append(catalog.preview_publication(db, item.id, item.expected_version))
            except catalog.CatalogError as exc:
                items.append({"id": item.id, "eligible": False, "snapshot": None,
                              "product": None, "error": exc.detail})
    eligible = sum(item["eligible"] for item in items)
    return {"items": items, "eligible_count": eligible, "blocked_count": len(items) - eligible}


@router.post("/products/batch-publish")
def batch_publish(data: BatchPublication, actor: str = Depends(require_admin)):
    results = []
    for item in data.items:
        # Per-item transactions reuse every single-publication guard and audit.
        with SessionLocal() as db:
            try:
                action = ProductAction(**item.model_dump(exclude={"id"}), reason=data.reason)
                product = catalog.change_status(db, item.id, "publish", action, actor)
                results.append({"id": item.id, "ok": True, "product": product, "error": None})
            except catalog.CatalogError as exc:
                db.rollback()
                results.append({"id": item.id, "ok": False, "error": exc.detail, "status_code": exc.status_code})
            except Exception:
                db.rollback()
                logger.exception("Batch publication failed for product %s", item.id)
                results.append({"id": item.id, "ok": False, "error": "发布结果无法确认，请刷新商品状态后重试。", "status_code": 500})
    success = sum(item["ok"] for item in results)
    return {"items": results, "success_count": success, "failed_count": len(results) - success}


@router.get("/products/{product_id}")
def detail(product_id: int, db: Session = Depends(get_db)):
    return call(catalog.get_product_detail, db, product_id)


@router.get("/products/{product_id}/history")
def history(product_id: int, kind: Literal["revisions", "inspections", "audits"], before: int | None = Query(default=None, ge=1),
           limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)):
    return call(catalog.get_product_history, db, product_id, kind, before, limit)


@router.put("/products/{product_id}")
def update(product_id: int, data: ProductUpdate, actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    return call(catalog.update_product, db, product_id, data, actor)


@router.post("/products/{product_id}/inspect")
def inspect(product_id: int, data: ProductInspect, actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    return inspect_one(db, product_id, data.expected_version, data.mode, actor)


@router.post("/products/{product_id}/actions/{action}")
def action(product_id: int, action: Literal["publish", "offline", "reject", "submit"], data: ProductAction,
           actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    return call(catalog.change_status, db, product_id, action, data, actor)


@router.post("/jobs", status_code=202)
def create_job(data: JobRequest, actor: str = Depends(require_admin), db: Session = Depends(get_db)):
    try: return jobs.enqueue(db, [x.model_dump() for x in data.items], data.mode, data.idempotency_key, actor)
    except (ValueError, LookupError) as exc: raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/jobs")
def list_job(limit: int = Query(default=20, ge=1, le=100), before: str | None = None, db: Session = Depends(get_db)):
    try:
        return jobs.list_jobs(db, limit, before)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(jobs.InspectionJob, job_id)
    if not job: raise HTTPException(status_code=404, detail="任务不存在")
    return jobs.serialize_job(db, job)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, db: Session = Depends(get_db)):
    try: return jobs.cancel(db, job_id)
    except LookupError as exc: raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/inspections/{task_id}/report")
def inspection_report(task_id: str, db: Session = Depends(get_db)):
    from app.main import result
    return result(task_id, db)


@router.get("/inspections/{task_id}/traces")
def inspection_traces(task_id: str, db: Session = Depends(get_db)):
    from app.main import traces
    return traces(task_id, db)
