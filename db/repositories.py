from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.schemas import InspectionReport, ProductInput
from db.models import AgentTrace, InspectionResult, InspectionTask


def create_task(
    db: Session,
    product: ProductInput,
    trigger_source: str,
    *,
    idempotency_key: str | None = None,
) -> InspectionTask:
    if idempotency_key:
        existing = db.scalar(select(InspectionTask).where(
            InspectionTask.idempotency_key == idempotency_key,
            InspectionTask.trigger_source == trigger_source,
        ).order_by(InspectionTask.id.desc()))
        if existing is not None:
            return existing
    payload = product.model_dump(mode="json")
    input_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    task = InspectionTask(
        task_id=f"task_{uuid4().hex}",
        product_id=product.product_id,
        status="running",
        trigger_source=trigger_source,
        input_json=payload,
        input_hash=input_hash,
        idempotency_key=idempotency_key,
    )
    db.add(task)
    db.commit()
    return task


def save_success(db: Session, task: InspectionTask, report: InspectionReport, traces: list[dict] | None = None) -> None:
    task.status = report.status
    task.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    result = InspectionResult(
        task_id=task.task_id,
        risk_level=report.risk_level,
        score=report.score,
        issues=[issue.model_dump() for issue in report.issues],
        optimized_title=report.optimized_title,
        optimized_description=report.optimized_description,
        report_json=report.model_dump(),
    )
    db.add(result)
    for trace in traces or []:
        db.add(AgentTrace(task_id=task.task_id, **trace))
    db.commit()


def save_failure(db: Session, task: InspectionTask, exc: Exception, traces: list[dict] | None = None) -> None:
    db.rollback()
    task.status = "failed"
    task.error_message = f"{type(exc).__name__}: 质检未完成，请检查输入或服务状态。"
    task.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    for trace in traces or []:
        db.add(AgentTrace(task_id=task.task_id, **trace))
    db.commit()


def get_task(db: Session, task_id: str) -> InspectionTask | None:
    return db.scalar(select(InspectionTask).where(InspectionTask.task_id == task_id))


def get_result(db: Session, task_id: str) -> InspectionResult | None:
    return db.scalar(select(InspectionResult).where(InspectionResult.task_id == task_id))
