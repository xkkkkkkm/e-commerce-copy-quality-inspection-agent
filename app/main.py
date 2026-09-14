import json
import os
import httpx
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from agent.orchestrator import inspect_product
from app.schemas import InspectionReport, ProductInput, TaskResponse
from db.models import AgentTrace
from db.repositories import create_task, get_result, get_task, save_failure, save_success
from db.session import Base, engine, get_db, SessionLocal
from db.migrations import run_migrations
from rag import RuleRetriever
from app.admin import router as admin_router
from app.simulation import router as simulation_router
from app.admin_auth import require_admin, router as admin_auth_router
from db import auth_models, catalog_models  # noqa: F401: register new tables
from db import job_models  # noqa: F401: register durable inspection queue tables
from app.tenant_middleware import TenantMiddleware
from app.body_limit import BodyLimitMiddleware
from services.tenancy import evaluation_path
from services.observability import (ObservabilityMiddleware, INSPECTIONS, INSPECTION_SECONDS, event)


ROOT = Path(__file__).resolve().parents[1]
EVALUATION_LOCK = Lock()


@asynccontextmanager
async def lifespan(app):
    from services.tenancy import tenant_names, tenant_scope
    from db.session import current_engine
    for name in tenant_names():
        with tenant_scope(name):
            bind = current_engine()
            run_migrations(bind)
            Base.metadata.create_all(bind=bind)
    yield


app = FastAPI(title="E-commerce Copy Quality Inspection Agent", version="2.0.0", lifespan=lifespan)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        declared = request.headers.get("content-length")
        max_body = int(os.getenv("APP_MAX_BODY_BYTES", "262144"))
        if declared and declared.isdigit() and int(declared) > max_body:
            response = Response("请求体过大", status_code=413, media_type="text/plain")
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "same-origin"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'self'; form-action 'self'"
            )
            return response
        response = await call_next(request)
        # HTML and unversioned assets must revalidate together after deployment.
        # Otherwise cached markup can load a newer script with incompatible IDs.
        if request.url.path in {"/", "/admin"} or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        return response


app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(TenantMiddleware)
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(ObservabilityMiddleware)
app.mount("/static", StaticFiles(directory=ROOT / "app/static"), name="static")
app.include_router(admin_auth_router)
app.include_router(admin_router)
app.include_router(simulation_router)


@app.get("/admin", include_in_schema=False)
def admin_page():
    return FileResponse(ROOT / "app/static/admin.html")


@app.get("/", include_in_schema=False)
def demo():
    return FileResponse(ROOT / "app/static/index.html")


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="MySQL unavailable") from exc
    search = "unavailable"
    try:
        from rag.elasticsearch import ElasticsearchSettings
        settings = ElasticsearchSettings.from_env()
        with httpx.Client(timeout=settings.timeout, follow_redirects=False) as client:
            response = client.get(f"{settings.url}/_cluster/health", headers=settings.headers,
                                  auth=settings.auth, timeout=settings.timeout)
            response.raise_for_status()
            search = "available"
    except Exception:
        pass
    return {"status": "ok", "service": "quality-agent", "database": "mysql", "elasticsearch": search}


def perform_inspection(product: ProductInput, db: Session, mode: str, trigger_source="api",
                       task_id: str | None = None) -> InspectionReport:
    task = get_task(db, task_id) if task_id else create_task(db, product, trigger_source)
    if task is None:
        raise RuntimeError("质检任务不存在")
    if task_id and task.product_id != product.product_id:
        raise RuntimeError("质检任务与商品不匹配")
    db.commit()  # No SQL transaction/connection held during external model I/O.
    traces = []
    import time
    started = time.monotonic()
    try:
        retriever = RuleRetriever()
        report = inspect_product(product, task.task_id, lambda **event: traces.append(event),
                                 mode=mode, retriever=retriever)
        report.rule_version = retriever.last_version
        report.rule_source = retriever.last_source
        save_success(db, task, report, traces)
        INSPECTIONS.labels(mode, "degraded" if report.degraded else report.status).inc()
        event("inspection_completed", task_id=task.task_id, mode=mode, status=report.status)
        return report
    except Exception as exc:
        INSPECTIONS.labels(mode, "failure").inc()
        traces.append(dict(step_name="inspection_failed", skill_name=None, tool_name=None,
                           input_summary=f"product_id={product.product_id}", output_summary=type(exc).__name__,
                           latency_ms=0, status="failure"))
        save_failure(db, task, exc, traces)
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=422, detail="无法可靠识别类目，请明确填写食品、美妆或3C。") from exc
        raise HTTPException(status_code=500, detail="质检失败，请检查服务状态。") from exc
    finally:
        INSPECTION_SECONDS.labels(mode).observe(time.monotonic() - started)


@app.get("/metrics", include_in_schema=False)
def metrics():
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/api/products/inspect", response_model=InspectionReport)
def inspect(product: ProductInput, mode: Literal["rules", "full"] = "full", db: Session = Depends(get_db)):
    return perform_inspection(product, db, mode)


def protect_managed_task(task_id: str, request: Request, db: Session = Depends(get_db)):
    task = get_task(db, task_id)
    if task and task.trigger_source == "admin":
        require_admin(request, db)


@app.get("/api/tasks/{task_id}", response_model=TaskResponse, dependencies=[Depends(protect_managed_task)])
def task_status(task_id: str, db: Session = Depends(get_db)):
    task = get_task(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@app.get("/api/results/{task_id}", response_model=InspectionReport, dependencies=[Depends(protect_managed_task)])
def result(task_id: str, db: Session = Depends(get_db)):
    report = get_result(db, task_id)
    if not report:
        raise HTTPException(status_code=404, detail="质检结果不存在")
    return InspectionReport.model_validate(report.report_json)


@app.get("/api/tasks/{task_id}/traces", dependencies=[Depends(protect_managed_task)])
def traces(task_id: str, db: Session = Depends(get_db)):
    if not get_task(db, task_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    records = db.scalars(select(AgentTrace).where(AgentTrace.task_id == task_id).order_by(AgentTrace.id)).all()
    fields = ("id", "task_id", "step_name", "skill_name", "tool_name", "input_summary",
              "output_summary", "status", "latency_ms", "created_at")
    return [{name: getattr(record, name) for name in fields} for record in records]


@app.get("/api/results/{task_id}/export", dependencies=[Depends(protect_managed_task)])
def export_report(task_id: str, db: Session = Depends(get_db)):
    report = result(task_id, db)
    content = json.dumps({"report": report.model_dump(), "traces": traces(task_id, db)},
                         ensure_ascii=False, indent=2, default=str)
    return Response(content, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{report.task_id}.json"'})


@app.get("/api/rules")
def rules(category: Literal["食品", "美妆", "3C"], query: str = "", issue_type: str | None = None,
          top_k: int = Query(default=5, ge=1, le=100)):
    retriever = RuleRetriever()
    results = retriever.retrieve(category, query, top_k, issue_type)
    return {"rules": results, "source": retriever.last_source, "warning": retriever.last_warning}


@app.post("/api/evaluations/run")
def evaluate(mode: Literal["rules", "full"] = "rules", username: str = Depends(require_admin),
             db: Session = Depends(get_db)):
    from evaluator.run_eval import DEFAULT_REPORT, load_cases, persist_results, run_evaluation, write_report
    DEFAULT_REPORT = evaluation_path(DEFAULT_REPORT)

    if not EVALUATION_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有评测正在执行，请稍后查看结果。")
    try:
        cases = load_cases()
        # Authentication is complete; do not retain its request session while
        # all 50 external inspections execute.  Each case gets a short session.
        db.close()

        def evaluate_one(product, task_id):
            with SessionLocal() as item_db:
                return perform_inspection(product, item_db, mode, "evaluation")

        report = run_evaluation(cases, evaluate_one,
                                mode=mode, transport="api")
        write_report(report, DEFAULT_REPORT)
        try:
            persist_results(cases, report)
            report["config"]["persistence_status"] = "success"
        except Exception as exc:
            report["config"]["persistence_status"] = "failed"
            report["config"]["persistence_error"] = f"{type(exc).__name__}: 评测明细入库失败，JSON报告已保留。"
        write_report(report, DEFAULT_REPORT)
        return report
    finally:
        EVALUATION_LOCK.release()


@app.get("/api/evaluations/latest")
def latest_evaluation():
    from evaluator.run_eval import DEFAULT_REPORT
    DEFAULT_REPORT = evaluation_path(DEFAULT_REPORT)

    if not DEFAULT_REPORT.exists():
        raise HTTPException(status_code=404, detail="尚未执行评测")
    return json.loads(DEFAULT_REPORT.read_text(encoding="utf-8"))
