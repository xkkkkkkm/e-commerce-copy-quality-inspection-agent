"""Bounded, reproducible multi-tenant ingress (10,000+ synthetic products).

API mode uses a private client credential file; database mode is operator-only.
Each request is <=100 products, with bounded concurrency and optional pacing.
No model calls or publication unless inspections are explicitly requested.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import time
from threading import Lock

import httpx
from services.simulation import DeliveryRequest, deliver_batch, generate_preview
from services.tenancy import tenant_scope


def chunks(count, tenants, batch, size=100):
    if count < 1 or count > 1_000_000 or not tenants or not 1 <= size <= 100:
        raise ValueError("Count 1..1000000, chunk 1..100 and at least one tenant required")
    for offset in range(0, count, size):
        ordinal = offset // size
        yield tenants[ordinal % len(tenants)], f"{batch}-{ordinal:06d}", min(size, count - offset)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10000)
    parser.add_argument("--tenants", default="default", help="Comma-separated registered tenant slugs")
    parser.add_argument("--seed", type=int, default=20260914)
    parser.add_argument("--batch-id", default="scale-20260914")
    parser.add_argument("--chunk-size", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--requests-per-second", type=float, default=2)
    parser.add_argument("--delivery", choices=("database", "api", "export"), default="export")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--clients-file", default=".local/load-clients.json")
    parser.add_argument("--output", default="simulation-output/load.jsonl")
    parser.add_argument("--inspect", action="store_true", help="Enqueue deterministic rules baseline; consumes no LLM tokens")
    args = parser.parse_args()
    if not 1 <= args.concurrency <= 16 or not 0 < args.requests_per_second <= 100:
        parser.error("Use concurrency 1..16 and requests-per-second (0,100]")
    tenants = args.tenants.split(",")
    # Validate entire plan before writing anything.
    plan = list(chunks(args.count, tenants, args.batch_id, args.chunk_size))
    clients = json.loads(Path(args.clients_file).read_text()) if args.delivery == "api" else {}
    pacing_lock = Lock()
    next_request = time.monotonic()

    def deliver(entry):
        nonlocal next_request
        tenant, batch, count = entry
        with pacing_lock:
            delay = max(0, next_request - time.monotonic())
            next_request = max(next_request, time.monotonic()) + 1 / args.requests_per_second
        time.sleep(delay)
        request = DeliveryRequest(count=count, seed=args.seed, batch_id=batch, enqueue_inspection=args.inspect)
        with tenant_scope(tenant):
            if args.delivery == "database":
                from db.session import SessionLocal
                with SessionLocal() as db:
                    result = deliver_batch(db, request, "synthetic-load-generator")
            else:
                credential = clients.get(tenant)
                if credential is None and tenant == "default":
                    credential = {"username": os.getenv("ADMIN_USERNAME", "admin"),
                                  "password": os.getenv("ADMIN_PASSWORD", "admin12345")}
                if credential is None:
                    raise RuntimeError(f"No client credential for {tenant}")
                with httpx.Client(base_url=args.base_url, timeout=120, follow_redirects=False) as client:
                    login = client.post("/api/admin/auth/login", params={"tenant": tenant}, json=credential)
                    if login.status_code != 200:
                        raise RuntimeError(f"Login failed for {tenant}: HTTP {login.status_code}")
                    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
                    try:
                        for attempt in range(4):
                            response = client.post("/api/admin/simulation/deliver", json=request.model_dump())
                            if response.status_code != 429:
                                break
                            time.sleep(min(30, 2 ** attempt))
                        if response.status_code != 200:
                            raise RuntimeError(f"Delivery failed: HTTP {response.status_code}; resume with same batch-id")
                        result = response.json()
                    finally:
                        client.post("/api/admin/auth/logout")
        return {"tenant": tenant, "created": result["created_count"], "existing": result["existing_count"]}

    started = time.monotonic()
    if args.delivery == "export":
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            for tenant, batch, count in plan:
                for row in generate_preview({"count": count, "seed": args.seed, "batch_id": batch})["products"]:
                    handle.write(json.dumps({"tenant": tenant, **row}, ensure_ascii=False) + "\n")
        print(json.dumps({"event": "synthetic_export", "count": args.count, "path": str(path)}))
        return
    totals = {tenant: {"created": 0, "existing": 0} for tenant in tenants}
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        # Submit only a bounded window, not one Future per product.
        for offset in range(0, len(plan), args.concurrency):
            futures = [pool.submit(deliver, entry) for entry in plan[offset:offset + args.concurrency]]
            for future in as_completed(futures):
                result = future.result()
                for key in ("created", "existing"):
                    totals[result["tenant"]][key] += result[key]
            print(json.dumps({"event": "synthetic_ingress_progress", "tenants": totals}), flush=True)
    print(json.dumps({"event": "synthetic_ingress_complete", "count": args.count,
                      "seconds": round(time.monotonic() - started, 2), "tenants": totals}))


if __name__ == "__main__":
    main()
