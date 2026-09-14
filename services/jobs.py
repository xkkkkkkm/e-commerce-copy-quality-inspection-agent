"""Durable administrator inspection queue."""
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime, timedelta, timezone
import json
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.catalog_models import ManagedProduct
from db.job_models import InspectionJob, InspectionJobItem

MAX_ATTEMPTS = 3
LEASE_SECONDS = 300


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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
    return db.scalar(select(InspectionJob).where(InspectionJob.id == job_id).with_for_update())


def _close_if_done(db: Session, job: InspectionJob, now: datetime | None = None) -> None:
    now = now or _now()
    active = db.scalar(select(InspectionJobItem.id).where(
        InspectionJobItem.job_id == job.id,
        InspectionJobItem.status.in_(["queued", "running", "retry"])))
    if active:
        return
    failed = db.scalar(select(InspectionJobItem.id).where(
        InspectionJobItem.job_id == job.id, InspectionJobItem.status == "failed"))
    cancelled = db.scalar(select(InspectionJobItem.id).where(
        InspectionJobItem.job_id == job.id, InspectionJobItem.status == "cancelled"))
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


def claim(db: Session, owner: str, lease_seconds: int = LEASE_SECONDS):
    """Claim one item; the job row lock serializes claims per job."""
    now = _now()
    candidates = db.scalars(select(InspectionJob).where(
        InspectionJob.status.in_(["queued", "running"]),
        InspectionJob.cancel_requested.is_(False)).order_by(
            InspectionJob.created_at, InspectionJob.id).with_for_update(skip_locked=True)).all()
    for job in candidates:
        if job.status == "running" and job.lease_until and job.lease_until > now:
            continue
        item = db.scalar(select(InspectionJobItem).where(
            InspectionJobItem.job_id == job.id,
            InspectionJobItem.status.in_(["queued", "retry"]),
            InspectionJobItem.attempts < MAX_ATTEMPTS).order_by(InspectionJobItem.id)
                         .with_for_update(skip_locked=True))
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
        job.lease_owner = owner
        job.lease_until = now + timedelta(seconds=max(1, int(lease_seconds)))
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
    job = _lock_job(db, item.job_id)
    item = db.scalar(select(InspectionJobItem).where(InspectionJobItem.id == item_id).with_for_update())
    if not job or not item or job.lease_owner != owner or item.status != "running" or not job.lease_until or job.lease_until <= now:
        db.rollback(); return False
    job.lease_until = now + timedelta(seconds=max(1, int(lease_seconds)))
    db.commit(); return True


def recover_expired(db: Session) -> int:
    now = _now()
    candidates = db.scalars(select(InspectionJob).where(
        InspectionJob.status == "running", InspectionJob.lease_until <= now)
        .with_for_update(skip_locked=True)).all()
    recovered = 0
    for job in candidates:
        running = db.scalars(select(InspectionJobItem).where(
            InspectionJobItem.job_id == job.id, InspectionJobItem.status == "running")
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
        job.lease_owner = None
        job.lease_until = None
        job.status = "queued"
        recovered += len(running)
        db.flush()
        _close_if_done(db, job, now)
    db.commit()
    return recovered


def finish_item(db: Session, item_id: int, *, ok: bool, task_id=None,
                inspection_id=None, error=None, owner: str | None = None):
    """Commit a result only while the worker owns an unexpired lease."""
    now = _now()
    item = db.scalar(select(InspectionJobItem).where(InspectionJobItem.id == item_id))
    if not item:
        db.rollback(); return False
    job = _lock_job(db, item.job_id)
    item = db.scalar(select(InspectionJobItem).where(InspectionJobItem.id == item_id).with_for_update())
    if not job or item.status != "running" or (owner is not None and job.lease_owner != owner) or not job.lease_until or job.lease_until <= now:
        db.rollback(); return False
    item.task_id = task_id or item.task_id
    item.inspection_id = inspection_id
    item.error_message = error
    item.status = "success" if ok else ("retry" if item.attempts < MAX_ATTEMPTS and not job.cancel_requested else "failed")
    if item.status in {"success", "failed"}:
        item.finished_at = now
    job.lease_owner = None
    job.lease_until = None
    job.status = "queued" if item.status == "retry" else "running"
    db.flush()
    _close_if_done(db, job, now)
    db.commit()
    return True
