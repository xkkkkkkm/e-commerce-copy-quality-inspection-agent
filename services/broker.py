"""Redis Streams transport with the MySQL item table as a recoverable outbox.

Publish-before-stamp permits duplicate delivery. Stable IDs + SQL lease claims
make duplicates harmless. Queued rows are republished after 30s, so a Redis
flush/restart cannot permanently strand committed jobs. Never trim pending data.
"""
from datetime import timedelta
from functools import lru_cache
import os

import redis
from sqlalchemy import or_, select
from db.job_models import InspectionJobItem
from services import jobs
from services.tenancy import tenant_id

GROUP = "inspectors"


@lru_cache(maxsize=1)
def client():
    return redis.Redis.from_url(os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0"),
                               decode_responses=True, socket_timeout=3, socket_connect_timeout=2)


def stream():
    return "qa:inspection:" + tenant_id.get()


def ensure_group():
    try:
        client().xgroup_create(stream(), GROUP, id="0", mkstream=True)
    except redis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


def dispatch(db, limit=100):
    ensure_group()
    now = jobs._now()
    rows = db.scalars(select(InspectionJobItem).where(
        InspectionJobItem.status.in_(["queued", "retry"]),
        or_(InspectionJobItem.next_attempt_at.is_(None), InspectionJobItem.next_attempt_at <= now),
        or_(InspectionJobItem.dispatched_at.is_(None), InspectionJobItem.dispatched_at < now - timedelta(seconds=30)))
        .order_by(InspectionJobItem.id).limit(limit).with_for_update(skip_locked=True)).all()
    for row in rows:
        client().xadd(stream(), {"item_id": str(row.id)})
        row.dispatched_at = now
    db.commit()
    return len(rows)


def receive(owner):
    ensure_group()
    reclaimed = client().xautoclaim(stream(), GROUP, owner, min_idle_time=jobs.LEASE_SECONDS * 1000,
                                  start_id="0-0", count=1)
    if reclaimed[1]:
        return reclaimed[1][0]
    rows = client().xreadgroup(GROUP, owner, {stream(): ">"}, count=1)
    return rows[0][1][0] if rows else None


def acknowledge(message_id):
    pipeline = client().pipeline()
    pipeline.xack(stream(), GROUP, message_id)
    pipeline.xdel(stream(), message_id)
    pipeline.execute()
