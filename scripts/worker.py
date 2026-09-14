"""Durable inspection worker: ``python -m scripts.worker``."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import os
import socket
import threading
import time
from uuid import uuid4

from sqlalchemy import select

from db.catalog_models import ManagedProduct, ProductInspection
from db.models import InspectionTask
from db.job_models import InspectionJob, InspectionJobItem
from db.session import SessionLocal
from services import catalog, jobs
from services import broker
from services.tenancy import tenant_scope, tenant_names, tenant_id
from services.observability import (configure_logging, event, WORKER_ACTIVE, WORKER_HEARTBEAT,
                                    QUEUE_ITEMS, QUEUE_AGE, TENANT_RECONCILE_FAILURES)


def _renew_loop(item_id: int, owner: str, stop: threading.Event, interval: float, tenant: str) -> None:
    while not stop.wait(interval):
        try:
            with tenant_scope(tenant), SessionLocal() as heartbeat_db:
                if not jobs.renew(heartbeat_db, item_id, owner):
                    return
        except Exception as exc:
            event("lease_renewal_failed", tenant_id=tenant, item_id=item_id, error_code=type(exc).__name__)
            return


def execute_once(db, owner: str, *, item_id: int | None = None) -> bool:
    item_id = jobs.claim(db, owner, item_id=item_id)
    if item_id is None:
        return False
    item = db.get(InspectionJobItem, item_id)
    if item is None:
        return True
    task_id = item.task_id
    job_id = item.job_id
    mode = item.mode
    job = db.get(InspectionJob, job_id)
    if job is None:
        db.rollback()
        return True
    actor = job.actor
    # The catalog runner creates/links the durable task id.  Keep the item
    # transaction closed before retrieval/model calls.
    db.commit()
    # Crash recovery path: catalog.inspect_product may have committed the
    # report before the worker lost its lease. Reuse that durable result rather
    # than creating a second inspection for the same queue item.
    committed = db.scalar(select(ProductInspection).where(
        ProductInspection.task_id == task_id).order_by(ProductInspection.id.desc()))
    if committed is not None and committed.status == "success":
        current = db.get(ManagedProduct, item.managed_product_id)
        applied = bool(current and current.latest_inspection_id == committed.id
                       and current.inspected_version == item.expected_version
                       and catalog._complete(committed))
        with SessionLocal() as finish_db:
            jobs.finish_item(finish_db, item_id, ok=applied, task_id=task_id,
                             inspection_id=committed.id,
                             error=None if applied else "商品版本已变化，结果仅归档",
                             owner=owner)
        return True
    # A crash can leave an unfinished durable task. Do not insert its unique
    # task_id again or share result writes with a late previous attempt. Keep
    # that attempt as history and reserve a new execution under the item fence.
    if db.scalar(select(InspectionTask.id).where(InspectionTask.task_id == task_id)) is not None:
        db.rollback()
        current_item = db.scalar(select(InspectionJobItem).where(InspectionJobItem.id == item_id)
                                 .with_for_update().execution_options(populate_existing=True))
        if (current_item.status != "running" or current_item.lease_owner != owner
                or not current_item.lease_until or current_item.lease_until <= jobs._now()):
            db.rollback()
            return True
        task_id = current_item.task_id = "task_" + uuid4().hex
        db.commit()
    stop = threading.Event()
    heartbeat = threading.Thread(target=_renew_loop,
                                 args=(item_id, owner, stop, max(5.0, jobs.LEASE_SECONDS / 3), tenant_id.get()),
                                 daemon=True)
    heartbeat.start()
    ok = False
    error = None
    inspection_id = None
    try:
        from app.main import perform_inspection
        # catalog.inspect_product enforces version/current-latest guards and
        # passes task_id to the runner before external I/O.
        result = catalog.inspect_product(db, item.managed_product_id, item.expected_version,
                                         mode, actor, perform_inspection, task_id=task_id)
        ok = (result.get("inspection", {}).get("status") == "success"
              and bool(result.get("applied"))
              and bool(result.get("product", {}).get("inspection_complete")))
        inspection_id = result.get("inspection", {}).get("id")
    except Exception as exc:
        error = type(exc).__name__
        db.rollback()
    finally:
        stop.set()
        heartbeat.join(timeout=2)
    # External execution may have left this Session in a failed transaction;
    # completion always uses a fresh short transaction and lease fencing.
    with SessionLocal() as finish_db:
        if inspection_id is None:
            inspection_id = finish_db.scalar(select(ProductInspection.id).where(
                ProductInspection.task_id == task_id).order_by(ProductInspection.id.desc()))
        accepted = jobs.finish_item(finish_db, item_id, ok=ok, task_id=task_id,
                         inspection_id=inspection_id, error=error, owner=owner)
        completed = finish_db.get(InspectionJobItem, item_id)
        event("inspection_job_item_finished", item_id=item_id, job_id=job_id, task_id=task_id,
              status=completed.status, error_code=error, mode=mode)
    return True


def consume(tenant, owner):
    with tenant_scope(tenant):
        message = broker.receive(owner)
        if not message:
            return False
        message_id, payload = message
        with WORKER_ACTIVE.track_inprogress(), SessionLocal() as db:
            execute_once(db, owner + ":" + uuid4().hex[:8], item_id=int(payload["item_id"]))
        # Retry is durable in SQL; the dispatcher will send it when due.
        broker.acknowledge(message_id)
        return True


def reconcile():
    from sqlalchemy import func
    counts = {status: 0 for status in ("queued", "running", "retry", "failed", "success", "cancelled")}
    oldest = 0
    failures = 0
    for tenant in tenant_names():
        try:
            with tenant_scope(tenant), SessionLocal() as db:
                jobs.recover_expired(db)
                broker.dispatch(db)
                for status, count in db.execute(select(InspectionJobItem.status, func.count()).group_by(InspectionJobItem.status)):
                    counts[status] = counts.get(status, 0) + count
                first = db.scalar(select(func.min(InspectionJobItem.created_at)).where(
                    InspectionJobItem.status.in_(["queued", "running", "retry"])))
                if first:
                    oldest = max(oldest, (jobs._now() - first).total_seconds())
        except Exception as exc:
            failures += 1
            event("tenant_reconciliation_failed", tenant_id=tenant, error_code=type(exc).__name__)
            continue
    for status, count in counts.items():
        QUEUE_ITEMS.labels(status).set(count)
    QUEUE_AGE.set(oldest)
    TENANT_RECONCILE_FAILURES.set(failures)
    if failures < len(tenant_names()):
        WORKER_HEARTBEAT.set(time.time())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll", type=float, default=0.1)
    args = parser.parse_args()
    configure_logging()
    from prometheus_client import start_http_server
    start_http_server(int(os.getenv("WORKER_METRICS_PORT", "9101")))
    owner = f"{socket.gethostname()}:{uuid4().hex[:8]}"
    concurrency = max(1, min(32, int(os.getenv("WORKER_CONCURRENCY", "4"))))
    with ThreadPoolExecutor(max_workers=concurrency) as pool, ThreadPoolExecutor(max_workers=1) as reconciler:
        pending = set()
        cursor = 0
        last_reconcile = 0
        reconciliation = None
        while True:
            try:
                for future in tuple(pending):
                    if future.done():
                        pending.remove(future)
                        try:
                            future.result()
                        except Exception as exc:
                            event("worker_item_failed", error_code=type(exc).__name__)
                if time.monotonic() - last_reconcile > 2 and (reconciliation is None or reconciliation.done()):
                    last_reconcile = time.monotonic()
                    if reconciliation:
                        try:
                            reconciliation.result()
                        except Exception as exc:
                            event("worker_reconciliation_failed", error_code=type(exc).__name__)
                    reconciliation = reconciler.submit(reconcile)
                    if args.once:
                        reconciliation.result()
                names = tenant_names()
                while len(pending) < concurrency:
                    # Round-robin scheduling avoids a large tenant monopolizing
                    # all slots. Each task owns its SQL session and tenant scope.
                    tenant = names[cursor % len(names)]
                    cursor += 1
                    pending.add(pool.submit(consume, tenant, owner))
                if args.once:
                    for future in pending:
                        future.result()
                    return
            except Exception as exc:
                event("worker_iteration_failed", error_code=type(exc).__name__)
            time.sleep(max(.05, min(args.poll, 1)))


if __name__ == "__main__":
    main()
