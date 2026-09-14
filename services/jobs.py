"""Durable administrator inspection queue."""
from base64 import urlsafe_b64decode, urlsafe_b64encode
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
import random
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import and_, or_, select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.catalog_models import ManagedProduct
from db.job_models import InspectionJob, InspectionJobItem

MAX_ATTEMPTS = 3
LEASE_SECONDS = 300


class QueueFullError(ValueError):
    pass


@contextmanager
def _admission(db):
    from sqlalchemy import text
    # Admission uses exactly one connection for its advisory lock and all ORM
    # work. A waiting sender cannot consume the connection needed by the holder.
    db.rollback()
    with db.get_bind().connect() as connection:
        name = "qa_admission_" + sha256(str(connection.engine.url.database).encode()).hexdigest()[:32]
        if connection.scalar(text("SELECT GET_LOCK(:name, 5)"), {"name": name}) != 1:
            raise QueueFullError("Queue admission busy; retry later")
        try:
            connection.commit()  # End autobegin; MySQL named locks survive commit.
            with Session(bind=connection, autoflush=False, expire_on_commit=False) as admitted:
                yield admitted
        finally:
            connection.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": name})


def _check_capacity(db, count):
    active = db.scalar(select(func.count()).select_from(InspectionJobItem).where(
        InspectionJobItem.status.in_(["queued", "running", "retry"])))
    if active + count > int(os.getenv("TENANT_MAX_PENDING_ITEMS", "20000")):
        raise QueueFullError("Tenant queue capacity reached; retry later")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _observe_terminal(item):
    if item.status in {"success", "failed"}:
        from services.observability import JOB_ITEMS, JOB_LATENCY
        JOB_ITEMS.labels(item.status, item.mode).inc()
        JOB_LATENCY.labels(item.mode, item.status).observe(max(0, (_now() - item.created_at).total_seconds()))


def _job_id(key: str) -> str:
    return "job_" + sha256(key.encode("utf-8")).hexdigest()[:48]


def _payload_hash(items: list[dict], mode: str) -> str:
    canonical = [{"id": int(x["id"]), "expected_version": int(x["expected_version"])}
                 for x in sorted(items, key=lambda x: int(x["id"]))]
    return sha256(json.dumps({"items": canonical, "mode": mode}, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def enqueue(db: Session, items: list[dict], mode: str, idempotency_key: str, actor: str) -> dict:
    if mode not in {"rules", "full"} or not items or len(items) > 100:
        raise ValueError("invalid job payload")
    key = idempotency_key.strip()
    if not key or len(key) > 200:
        raise ValueError("idempotency_key required")
    ids = [int(x["id"]) for x in items]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate product in job")
    if any(int(x["expected_version"]) < 1 for x in items):
        raise ValueError("expected_version must be positive")
    payload_hash = _payload_hash(items, mode)
    jid = _job_id(key)
    existing = db.get(InspectionJob, jid)
    if existing:
        if existing.payload_hash != payload_hash:
            raise ValueError("idempotency_key already used with a different payload")
        return serialize_job(db, existing)
    # Serialize admission per tenant database, across API replicas. MySQL
    # advisory lock is released immediately after the durable enqueue commit.
    with _admission(db) as db:
        existing = db.get(InspectionJob, jid)
        if existing:
            if existing.payload_hash != payload_hash:
                raise ValueError("idempotency_key already used with a different payload")
            return serialize_job(db, existing)
        _check_capacity(db, len(items))
        return _enqueue_locked(db, items, mode, key, actor, jid, payload_hash, ids)


def _enqueue_locked(db, items, mode, key, actor, jid, payload_hash, ids):
    product_ids = set(db.scalars(select(ManagedProduct.id).where(ManagedProduct.id.in_(ids))).all())
    if product_ids != set(ids):
        raise LookupError("商品不存在")
    job = InspectionJob(id=jid, mode=mode, actor=actor, idempotency_key=key,
                        payload_hash=payload_hash)
    db.add(job)
    for x in items:
        db.add(InspectionJobItem(job_id=jid, managed_product_id=int(x["id"]),
                                 expected_version=int(x["expected_version"]), mode=mode))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.get(InspectionJob, jid)
        if existing is None:
            raise
        if existing.payload_hash != payload_hash:
            raise ValueError("idempotency_key already used with a different payload")
        return serialize_job(db, existing)
    return serialize_job(db, job)


def serialize_job(db: Session, job: InspectionJob) -> dict:
    rows = db.scalars(select(InspectionJobItem).where(
        InspectionJobItem.job_id == job.id).order_by(InspectionJobItem.id)).all()
    return {"id": job.id, "mode": job.mode, "status": job.status, "actor": job.actor,
            "attempts": job.attempts, "cancel_requested": bool(job.cancel_requested),
            "error_message": job.error_message, "created_at": job.created_at,
            "started_at": job.started_at, "finished_at": job.finished_at,
            "items": [{"id": r.managed_product_id, "expected_version": r.expected_version,
                        "status": r.status, "task_id": r.task_id,
                        "inspection_id": r.inspection_id, "attempts": r.attempts,
                        "error_message": r.error_message} for r in rows]}


def _cursor_value(created_at: datetime, job_id: str) -> str:
    return urlsafe_b64encode(f"{created_at.isoformat()}|{job_id}".encode()).decode().rstrip("=")


def _parse_cursor(db: Session, value: str) -> tuple[datetime, str]:
    try:
        raw = urlsafe_b64decode((value + "=" * (-len(value) % 4)).encode()).decode()
        timestamp, job_id = raw.split("|", 1)
        return datetime.fromisoformat(timestamp), job_id
    except Exception:
        job = db.get(InspectionJob, value)
        if not job:
            raise ValueError("invalid jobs cursor")
        return job.created_at, job.id


def list_jobs(db: Session, limit=20, before: str | None = None):
    limit = max(1, min(int(limit), 100))
    stmt = select(InspectionJob)
    if before:
        created_at, job_id = _parse_cursor(db, before)
        stmt = stmt.where(or_(InspectionJob.created_at < created_at,
                              and_(InspectionJob.created_at == created_at,
                                   InspectionJob.id < job_id)))
    rows = db.scalars(stmt.order_by(InspectionJob.created_at.desc(), InspectionJob.id.desc())
                      .limit(limit + 1)).all()
    return {"items": [serialize_job(db, x) for x in rows[:limit]],
            "next": _cursor_value(rows[limit - 1].created_at, rows[limit - 1].id)
            if len(rows) > limit else None}


def _lock_job(db: Session, job_id: str) -> InspectionJob | None:
    return db.scalar(select(InspectionJob).where(InspectionJob.id == job_id).with_for_update()
                     .execution_options(populate_existing=True))


def _close_if_done(db: Session, job: InspectionJob, now: datetime | None = None) -> None:
    now = now or _now()
    # Must be a current read: a worker may have opened a REPEATABLE READ
    # snapshot before waiting for the peer's job lock during finish_item.
    statuses = db.scalars(select(InspectionJobItem.status).where(
        InspectionJobItem.job_id == job.id).with_for_update()).all()
    if any(status in {"queued", "running", "retry"} for status in statuses):
        return
    failed = "failed" in statuses
    cancelled = "cancelled" in statuses
    job.status = "cancelled" if job.cancel_requested or cancelled else ("failed" if failed else "success")
    job.finished_at = job.finished_at or now
    job.lease_owner = None
    job.lease_until = None


def cancel(db: Session, job_id: str) -> dict:
    job = _lock_job(db, job_id)
    if not job:
        raise LookupError("任务不存在")
    job.cancel_requested = True
    db.execute(InspectionJobItem.__table__.update().where(
        InspectionJobItem.job_id == job.id,
        InspectionJobItem.status.in_(["queued", "retry"])).values(
            status="cancelled", finished_at=_now(), error_message="管理员取消"))
    _close_if_done(db, job)
    db.commit()
    return serialize_job(db, job)


def claim(db: Session, owner: str, lease_seconds: int = LEASE_SECONDS, item_id: int | None = None):
    """Short job->item locks; independent items hold independent fenced leases."""
    now = _now()
    stmt = select(InspectionJob).where(InspectionJob.status.in_(["queued", "running"]),
                                       InspectionJob.cancel_requested.is_(False))
    if item_id is not None:
        stmt = stmt.where(InspectionJob.id == select(InspectionJobItem.job_id).where(
            InspectionJobItem.id == item_id).scalar_subquery())
    candidates = db.scalars(stmt.order_by(InspectionJob.created_at, InspectionJob.id)
                           .limit(20).with_for_update(skip_locked=item_id is None)).all()
    for job in candidates:
        query = select(InspectionJobItem).where(
            InspectionJobItem.job_id == job.id,
            InspectionJobItem.status.in_(["queued", "retry"]),
            or_(InspectionJobItem.next_attempt_at.is_(None), InspectionJobItem.next_attempt_at <= now),
            InspectionJobItem.attempts < MAX_ATTEMPTS)
        if item_id is not None:
            query = query.where(InspectionJobItem.id == item_id)
        item = db.scalar(query.order_by(InspectionJobItem.id).limit(1).with_for_update(skip_locked=item_id is None))
        if not item:
            _close_if_done(db, job, now)
            continue
        item.status = "running"
        item.attempts += 1
        # Keep the durable execution id across lease retries. If a worker died
        # after the catalog transaction committed but before finish_item, the
        # next worker can observe that task and fence duplicate business work.
        item.task_id = item.task_id or f"task_{uuid4().hex}"
        item.error_message = None
        job.status = "running"
        job.attempts += 1
        item.lease_owner = owner
        item.lease_until = now + timedelta(seconds=max(1, int(lease_seconds)))
        job.started_at = job.started_at or now
        db.commit()
        return item.id
    # Persist terminal transitions discovered while scanning expired/exhausted
    # jobs even when no claim was available.
    db.commit()
    return None


def renew(db: Session, item_id: int, owner: str, lease_seconds: int = LEASE_SECONDS) -> bool:
    now = _now()
    item = db.scalar(select(InspectionJobItem).where(InspectionJobItem.id == item_id))
    if not item:
        db.rollback(); return False
    item = db.scalar(select(InspectionJobItem).where(InspectionJobItem.id == item_id).with_for_update()
                     .execution_options(populate_existing=True))
    if not item or item.lease_owner != owner or item.status != "running" or not item.lease_until or item.lease_until <= now:
        db.rollback(); return False
    item.lease_until = now + timedelta(seconds=max(1, int(lease_seconds)))
    db.commit(); return True


def recover_expired(db: Session) -> int:
    now = _now()
    candidates = db.scalars(select(InspectionJob).where(
        InspectionJob.status.in_(["running", "queued"]), InspectionJob.id.in_(
            select(InspectionJobItem.job_id).where(InspectionJobItem.status == "running",
                or_(InspectionJobItem.lease_until <= now, InspectionJobItem.lease_until.is_(None)))))
        .limit(100).with_for_update(skip_locked=True)).all()
    recovered = 0
    terminal = []
    for job in candidates:
        running = db.scalars(select(InspectionJobItem).where(
            InspectionJobItem.job_id == job.id, InspectionJobItem.status == "running",
            or_(InspectionJobItem.lease_until <= now, InspectionJobItem.lease_until.is_(None)))
            .with_for_update()).all()
        for item in running:
            if job.cancel_requested:
                item.status = "cancelled"
                item.error_message = "管理员取消"
                item.finished_at = now
            elif item.attempts >= MAX_ATTEMPTS:
                item.status = "failed"
                item.error_message = "租约过期且超过重试次数"
                item.finished_at = now
            else:
                item.status = "retry"
                item.error_message = "执行租约过期，等待重试"
                item.next_attempt_at = now + timedelta(seconds=2 ** item.attempts)
            item.lease_owner = None
            item.lease_until = None
            item.dispatched_at = None
            if item.status == "failed":
                terminal.append(item)
        job.lease_owner = None
        job.lease_until = None
        job.status = "queued"
        recovered += len(running)
        db.flush()
        _close_if_done(db, job, now)
    db.commit()
    for item in terminal:
        _observe_terminal(item)
    return recovered


def finish_item(db: Session, item_id: int, *, ok: bool, task_id=None,
                inspection_id=None, error=None, owner: str | None = None):
    """Commit a result only while the worker owns an unexpired lease."""
    now = _now()
    item = db.scalar(select(InspectionJobItem).where(InspectionJobItem.id == item_id))
    if not item:
        db.rollback(); return False
    job = _lock_job(db, item.job_id)
    item = db.scalar(select(InspectionJobItem).where(InspectionJobItem.id == item_id).with_for_update()
                     .execution_options(populate_existing=True))
    if not job or item.status != "running" or (owner is not None and item.lease_owner != owner) or not item.lease_until or item.lease_until <= now:
        db.rollback(); return False
    item.task_id = task_id or item.task_id
    item.inspection_id = inspection_id
    item.error_message = error
    item.status = "success" if ok else ("retry" if item.attempts < MAX_ATTEMPTS and not job.cancel_requested else "failed")
    if item.status in {"success", "failed"}:
        item.finished_at = now
    item.lease_owner = None
    item.lease_until = None
    item.dispatched_at = None
    if item.status == "retry":
        item.next_attempt_at = now + timedelta(seconds=min(60, 2 ** item.attempts + random.random()))
        if inspection_id is not None:
            # An incomplete/degraded committed report is archived. The next
            # attempt gets a new task; crash recovery of an unacknowledged
            # attempt still retains its stable task ID until finish commits.
            item.task_id = None
    job.status = "queued" if item.status == "retry" else "running"
    db.flush()
    _close_if_done(db, job, now)
    db.commit()
    _observe_terminal(item)
    return True


def retry_failed(db: Session, job_id: str):
    """Explicit operator retry, retaining product/version guards and task IDs."""
    with _admission(db) as db:
        job = _lock_job(db, job_id)
        if not job or job.status != "failed":
            raise ValueError("Only failed jobs can be retried")
        items = db.scalars(select(InspectionJobItem).where(
            InspectionJobItem.job_id == job_id, InspectionJobItem.status == "failed").with_for_update()).all()
        _check_capacity(db, len(items))
        for item in items:
            item.status, item.attempts = "queued", 0
            item.next_attempt_at = item.dispatched_at = item.finished_at = None
            if item.inspection_id is not None:
                item.task_id = None
        job.status, job.cancel_requested, job.finished_at = "queued", False, None
        db.commit()
        return serialize_job(db, job)
