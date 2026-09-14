"""Durable inspection worker: ``python -m scripts.worker``."""
import argparse
import socket
import threading
import time
from uuid import uuid4

from sqlalchemy import select

from db.catalog_models import ManagedProduct, ProductInspection
from db.job_models import InspectionJob, InspectionJobItem
from db.session import SessionLocal
from services import catalog, jobs


def _renew_loop(item_id: int, owner: str, stop: threading.Event, interval: float) -> None:
    while not stop.wait(interval):
        with SessionLocal() as heartbeat_db:
            if not jobs.renew(heartbeat_db, item_id, owner):
                return


def execute_once(db, owner: str) -> bool:
    item_id = jobs.claim(db, owner)
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
    stop = threading.Event()
    heartbeat = threading.Thread(target=_renew_loop,
                                 args=(item_id, owner, stop, max(5.0, jobs.LEASE_SECONDS / 3)),
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
        error = f"{type(exc).__name__}: {exc}"
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
        jobs.finish_item(finish_db, item_id, ok=ok, task_id=task_id,
                         inspection_id=inspection_id, error=error, owner=owner)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll", type=float, default=1.0)
    args = parser.parse_args()
    owner = f"{socket.gethostname()}:{uuid4().hex[:8]}"
    while True:
        with SessionLocal() as db:
            jobs.recover_expired(db)
            did_work = execute_once(db, owner)
        if args.once:
            return
        if not did_work:
            time.sleep(max(0.05, args.poll))


if __name__ == "__main__":
    main()
