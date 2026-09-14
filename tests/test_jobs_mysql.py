"""Opt-in MySQL tests for the durable inspection queue.

Only rows created by this module (unique product ids and deterministic job
keys) are removed by the fixture.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update

pytestmark = pytest.mark.skipif(os.getenv("RUN_MYSQL_TESTS") != "1",
                                reason="requires project MySQL; set RUN_MYSQL_TESTS=1")


@pytest.fixture
def queue_store():
    from db.catalog_models import ManagedProduct
    from db.job_models import InspectionJob, InspectionJobItem
    from db.session import Base, SessionLocal, engine

    Base.metadata.create_all(bind=engine, tables=[ManagedProduct.__table__,
                                                   InspectionJob.__table__,
                                                   InspectionJobItem.__table__])
    product_ids = ["job_test_" + uuid4().hex for _ in range(3)]
    with SessionLocal() as db:
        products = []
        for product_id in product_ids:
            product = ManagedProduct(product_id=product_id, merchant_name="queue-test",
                                     category="食品", title="queue test", description="test",
                                     attributes={}, status="pending", version=1)
            db.add(product)
            products.append(product)
        db.commit()
        ids = [x.id for x in products]
    try:
        yield ids
    finally:
        with SessionLocal() as db:
            managed_ids = list(db.scalars(select(ManagedProduct.id).where(
                ManagedProduct.product_id.in_(product_ids))))
            if managed_ids:
                jobs_for_products = list(db.scalars(select(InspectionJobItem.job_id).where(
                    InspectionJobItem.managed_product_id.in_(managed_ids))))
                if jobs_for_products:
                    db.execute(delete(InspectionJobItem).where(
                        InspectionJobItem.job_id.in_(jobs_for_products)))
                    db.execute(delete(InspectionJob).where(
                        InspectionJob.id.in_(jobs_for_products)))
                db.execute(delete(ManagedProduct).where(ManagedProduct.id.in_(managed_ids)))
            db.commit()


def _session():
    from db.session import SessionLocal
    return SessionLocal()


def _enqueue(db, ids, key="test-key", items=None):
    from services import jobs
    return jobs.enqueue(db, items or [{"id": ids[0], "expected_version": 1}],
                        "rules", key, "test:queue")


def test_idempotency_and_payload_mismatch(queue_store):
    from services import jobs
    with _session() as db:
        key = "idem-exact-" + uuid4().hex
        first = _enqueue(db, queue_store, key=key)
        assert _enqueue(db, queue_store, key=key)["id"] == first["id"]
        with pytest.raises(ValueError, match="different payload"):
            _enqueue(db, queue_store, key=key,
                     items=[{"id": queue_store[1], "expected_version": 1}])


def test_one_item_per_job_and_success(queue_store):
    from services import jobs
    from db.job_models import InspectionJob, InspectionJobItem
    with _session() as db:
        key = "claim-" + uuid4().hex
        created = _enqueue(db, queue_store, key=key,
                           items=[{"id": queue_store[0], "expected_version": 1},
                                  {"id": queue_store[1], "expected_version": 1}])
        first = jobs.claim(db, "worker-a", lease_seconds=60)
        assert first and jobs.claim(db, "worker-b", lease_seconds=60) is None
        item = db.get(InspectionJobItem, first)
        assert jobs.finish_item(db, first, ok=True, owner="worker-a", task_id=item.task_id)
        second = jobs.claim(db, "worker-b", lease_seconds=60)
        assert second and second != first
        assert jobs.finish_item(db, second, ok=True, owner="worker-b")
        job = db.get(InspectionJob, created["id"])
        assert job.status == "success"


def test_concurrent_workers_cannot_claim_same_job(queue_store):
    from services import jobs

    key = "concurrent-" + uuid4().hex
    with _session() as db:
        _enqueue(db, queue_store, key=key,
                 items=[{"id": queue_store[0], "expected_version": 1},
                        {"id": queue_store[1], "expected_version": 1}])

    def claim_for(owner):
        with _session() as worker_db:
            item_id = jobs.claim(worker_db, owner, lease_seconds=60)
            if item_id is None:
                # InnoDB SKIP LOCKED may observe the peer's row lock before
                # that transaction commits; retry once after it releases.
                time.sleep(0.05)
                item_id = jobs.claim(worker_db, owner, lease_seconds=60)
            return owner, item_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(claim_for, ("concurrent-a", "concurrent-b")))
    ids = [item_id for _, item_id in claimed if item_id is not None]
    assert len(ids) == 1


def test_expired_lease_recovery_and_fencing(queue_store):
    from services import jobs
    from db.job_models import InspectionJob, InspectionJobItem
    with _session() as db:
        created = _enqueue(db, queue_store, key="lease-" + uuid4().hex)
        item_id = jobs.claim(db, "old-worker", lease_seconds=60)
        db.execute(update(InspectionJob).where(InspectionJob.id == created["id"]).values(
            lease_until=jobs._now().replace(year=2000)))
        db.commit()
        assert jobs.recover_expired(db) == 1
        item = db.get(InspectionJobItem, item_id)
        assert item.status == "retry"
        new_id = jobs.claim(db, "new-worker", lease_seconds=60)
        assert new_id == item_id
        assert jobs.finish_item(db, item_id, ok=True, owner="old-worker") is False
        assert jobs.finish_item(db, item_id, ok=True, owner="new-worker") is True


def test_cancelled_running_job_expires_to_terminal_cancelled(queue_store):
    from db.job_models import InspectionJob, InspectionJobItem
    from services import jobs

    with _session() as db:
        created = _enqueue(db, queue_store, key="cancel-expired-" + uuid4().hex)
        item_id = jobs.claim(db, "worker", lease_seconds=60)
        assert item_id
        state = jobs.cancel(db, created["id"])
        assert state["status"] == "running"
        db.execute(update(InspectionJob).where(InspectionJob.id == created["id"]).values(
            lease_until=jobs._now().replace(year=2000)))
        db.commit()
        assert jobs.recover_expired(db) == 1
        db.expire_all()
        job = db.get(InspectionJob, created["id"])
        item = db.get(InspectionJobItem, item_id)
        assert job.status == "cancelled"
        assert item.status == "cancelled"
        assert job.finished_at is not None and job.lease_owner is None


def test_task_id_survives_lease_recovery_and_reclaim(queue_store):
    from db.job_models import InspectionJob, InspectionJobItem
    from services import jobs

    with _session() as db:
        created = _enqueue(db, queue_store, key="task-preserve-" + uuid4().hex)
        item_id = jobs.claim(db, "worker-a", lease_seconds=60)
        original = db.get(InspectionJobItem, item_id).task_id
        assert original
        db.execute(update(InspectionJob).where(InspectionJob.id == created["id"])
                   .values(lease_until=jobs._now().replace(year=2000)))
        db.commit()
        assert jobs.recover_expired(db) == 1
        assert jobs.claim(db, "worker-b", lease_seconds=60) == item_id
        assert db.get(InspectionJobItem, item_id).task_id == original


def test_jobs_pagination_has_no_duplicates_or_skips(queue_store):
    from services import jobs

    keys = ["page-" + uuid4().hex for _ in range(5)]
    with _session() as db:
        expected = {_enqueue(db, queue_store, key=key)["id"] for key in keys}
        cursor = None
        seen = []
        for _ in range(100):
            page = jobs.list_jobs(db, limit=2, before=cursor)
            seen.extend(row["id"] for row in page["items"] if row["id"] in expected)
            cursor = page["next"]
            if not cursor:
                break
        assert set(seen) == expected
        assert len(seen) == len(set(seen))


def test_cancel_queued_items_allows_running_item_to_finish(queue_store):
    from services import jobs
    from db.job_models import InspectionJob, InspectionJobItem
    with _session() as db:
        created = _enqueue(db, queue_store, key="cancel-" + uuid4().hex,
                           items=[{"id": queue_store[0], "expected_version": 1},
                                  {"id": queue_store[1], "expected_version": 1}])
        running = jobs.claim(db, "worker", lease_seconds=60)
        state = jobs.cancel(db, created["id"])
        assert state["items"][1]["status"] == "cancelled"
        assert jobs.finish_item(db, running, ok=True, owner="worker") is True
        job = db.get(InspectionJob, created["id"])
        assert job.status == "cancelled"


def test_bounded_failures(queue_store):
    from services import jobs
    from db.job_models import InspectionJobItem
    with _session() as db:
        _enqueue(db, queue_store, key="retry-" + uuid4().hex)
        item_id = jobs.claim(db, "worker", lease_seconds=60)
        for attempt in range(1, jobs.MAX_ATTEMPTS + 1):
            assert jobs.finish_item(db, item_id, ok=False, owner="worker", error="failure")
            item = db.get(InspectionJobItem, item_id)
            if attempt < jobs.MAX_ATTEMPTS:
                assert item.status == "retry"
                assert jobs.claim(db, "worker", lease_seconds=60) == item_id
            else:
                assert item.status == "failed"
