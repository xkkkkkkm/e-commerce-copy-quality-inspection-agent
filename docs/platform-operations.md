# Platform operations

Version 2 uses database-per-tenant isolation, LLM-led review and Redis Streams delivery. The historical documents describe the original single-tenant implementation; this guide and the root README take precedence for deployment and authentication.

## Configure and start

Copy `.env.example` to `.env` only when no local file exists. Set `DEEPSEEK_API_KEY` locally and choose a model advertised by the provider's `/models` API. The local verification used `deepseek-flash`; model availability is account-dependent. Do not put keys in command arguments, screenshots, issue reports or GitHub Actions logs.

`docker compose up --build -d --wait` starts MySQL, Elasticsearch, Redis, API and worker. A one-shot `runtime-config` service copies the operator tenant registry into an app-owned Docker volume with mode 0600. API and worker mount it read-only and run as the non-root `app` user. This avoids changing the host registry to world-readable to accommodate differing host/container UIDs.

Only the initialized MySQL container and operator provisioning workflow need database root authority. The API and worker receive default-tenant credentials and registered tenant-specific connections, not the root URL. This is application/process isolation, not isolation against a fully compromised API host which can access its configured tenant connections.

## Add a tenant

Run the following from a host Python virtual environment with project requirements installed. Supply a development/operator MySQL URL using a secret manager or a hidden shell prompt; never save that URL in a tracked file. The tenant password is prompted without echo unless `TENANT_ADMIN_PASSWORD` is already set.

```bash
read -rs MYSQL_ADMIN_URL
export MYSQL_ADMIN_URL
python -m scripts.provision_tenant merchant-east --username admin
unset MYSQL_ADMIN_URL
docker compose run --rm runtime-config
```

The default registry is `.local/runtime/tenants.json`, containing private database connections and salted PBKDF2 administrator password hashes. Keep `.local` private (0700) and the registry 0600. The root `.env` must also remain private. The provisioning command refuses existing tenants and databases; it never rotates credentials or replaces data implicitly. Run it serially. If provisioning is interrupted, inspect the created schema/user and registry before retrying; automatic destructive rollback is deliberately absent.

Each tenant has one administrator account in this version. Tenant creation, disabling or password rotation is operator-managed; SSO, multiple members, role delegation and a tenant-management UI are not implemented. Changing the tenant administrator password hash should be accompanied by session revocation. Take backups before registry edits, and distribute the updated registry with `runtime-config` again.

Login: `POST /api/admin/auth/login?tenant=merchant-east`, with username and password JSON. The response sets an HttpOnly, SameSite=Strict cookie and returns a CSRF token. Send it in `X-CSRF-Token` on mutations. Unknown tenants and forged cookie prefixes are rejected. `X-Tenant-ID` is not an authorization mechanism. All legacy inspection/report/trace/export/evaluation routes require authentication too.

Database bindings are selected before FastAPI enters its worker thread and captured when a Session is created. Async offloading propagates that context; dedicated worker and heartbeat threads enter their tenant scope explicitly. Each new schema has its own SQL user granted access to that schema only. Private tenant rules use tenant MySQL retrieval instead of the shared default Elasticsearch policy index. Evaluation JSON reports use separate tenant directories.

## Upgrade existing data

Back up the existing database with `mysqldump --single-transaction` using credentials from your local secret store. Keep the export under ignored `backups/` and test restoration independently. The local upgrade backup is not included in Git.

Stop the old worker, deploy the new image, then restart it after API migration succeeds. Do not run an old batch-lease worker alongside the new item-lease worker. `db/migrations.py` adds item lease, retry deadline and dispatch timestamp columns and copies active old batch leases onto running items. Existing tables/products/history remain in the default database. New tenants receive separate schemas and fixture copies.

Migrations are additive, idempotent MySQL bootstrap migrations, not an Alembic migration history. Run startup migrations through one deployment coordinator; concurrent first-start schema changes across API replicas are not supported. Rollback requires the prior image and compatible schema; do not remove lease columns or restore a database over newer writes without an explicit recovery plan.

A non-default tenant that fails startup initialization is unavailable (HTTP 503) while healthy tenants continue serving. Restore that tenant's database connectivity, then restart the API coordinator to retry migrations; restart workers after migration succeeds. The default database remains required for API startup. Worker reconciliation reports tenant failures separately and continues other tenants.

## Queue and concurrency

`POST /api/admin/jobs` accepts up to 100 products and an idempotency key, validates tenant ownership, commits MySQL job/items and returns 202. Matching replays return the original job; changed payloads conflict. Admission uses a tenant-specific MySQL advisory lock and a pending-item count to enforce `TENANT_MAX_PENDING_ITEMS` across API replicas. HTTP 429 tells senders to back off; it is not silent acceptance.

The item table doubles as a recoverable outbox. A coordinator publishes due queued/retry items to `qa:inspection:<tenant>` using Redis Streams. It stamps dispatch after publishing, deliberately allowing duplicates after a crash. Queued rows become eligible for retransmission after 30 seconds. Redis loss therefore does not erase the durable queue. Redis uses AOF and `noeviction`; failed publication is retried by reconciliation, not acknowledged as executed.

Consumer groups distribute messages. A worker obtains a short MySQL job/item lock, then owns an individual item lease while external work executes. Heartbeats renew that lease. Only its current owner may finish before expiry; stale owners cannot overwrite queue completion. Product version/current-inspection checks independently fence publication state. Final batch aggregation uses locking current reads, avoiding stale REPEATABLE READ snapshots.

Three attempts are allowed, with exponential delay and jitter. A completed degraded report is archived and the next attempt gets a new execution ID; an unacknowledged crash retry retains the prior ID to reuse already-committed catalog work. If the prior task exists but catalog completion was interrupted, the replacement worker reserves a new execution ID under its lease and preserves the old attempt as history. External calls are at-least-once, so a crash before durable completion can repeat a model request. Exhaustion leaves a queryable failed item. Administrators can explicitly `POST /api/admin/jobs/{job_id}/retry`; cancellation stops queued/retry items and lets running work finish safely. Retry is not permission to publish.

`WORKER_CONCURRENCY` defaults to 4 (1–32). Each item has its own Session; sessions are never shared across concurrent tasks. Scheduling cycles through configured tenants. Reconciliation runs separately and isolates individual tenant errors; a failed tenant is surfaced in metrics without halting healthy tenants. This is round-robin fairness, not hard per-tenant CPU/memory reservation. API Uvicorn accepts at most 128 concurrent connections/tasks; excess load can return 503.

`LLM_MAX_CONCURRENCY` defaults to 4 across API/worker replicas via Redis leases. `LLM_REQUESTS_PER_MINUTE` defaults to 60 actual HTTP attempts, including retry and JSON repair. Model calls have finite timeouts, at most two configured network retries and one JSON repair; Redis admission failure cannot silently bypass distributed limits. Capacity or model failures produce visible incomplete/degraded work that cannot pass full-mode publication.

The default Compose file runs one API and one worker process. Horizontal workers need independent metrics targets, sufficient database pool capacity and Redis capacity. Do not merely increase every limit: measure provider quotas, queue age, database connections, memory, cost and failure rate first.

## Synthetic ingress

`scripts.generate_load` supports 1–1,000,000 generated rows, batches no larger than 100, at most 16 simultaneous delivery requests and configurable request rate. Database delivery calls the versioned catalog service; API delivery logs in per batch and exercises HTTP validation, CSRF and admission. A private `--clients-file` maps tenant slugs to objects with `username` and `password`. It is a load-tool credential file, not application tenant configuration. Do not commit it.

Default generation is export-only and creates a new JSONL file without overwriting an existing export. Delivery defaults to no inspection; `--inspect` specifically requests the inexpensive deterministic baseline. Changed/edited products are rejected, not overwritten. Results preserve seed, batch, scenario, source and generator version. Fixed evaluation gold remains separate. This is synthetic workload validation; realistic distributions require business data and expert labels.

## Publication and remaining integration work

The LLM reviews original copy using applicable policy before deterministic checks. Its issues must cite exact input evidence and match the referenced rule's issue type/risk level. Mandatory safeguards, scoring, conservative rewrite and publication authority remain deterministic. Human review, current-version confirmation and an audit reason are still required.

Live marketplace integration needs signed webhooks/API credentials, external product IDs, replay protection and an outbound publication adapter. This version does not claim to publish to marketplaces. Production deployments also require TLS, secure cookies, proper secret storage, service network restrictions, backup restoration, monitoring recipients and a high-availability design. Keep `/metrics`, Prometheus, Grafana and Alertmanager off public ingress; metrics are aggregate operational data and intentionally do not use administrator sessions.
