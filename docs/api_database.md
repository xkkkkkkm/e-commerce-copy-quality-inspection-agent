# API and database

> Version 2 update: the [current platform guide](platform-operations.md) supersedes earlier single-tenant/public-API examples in this document. Current behavior uses LLM-led full-mode review, database-per-tenant isolation, authenticated data routes, Redis Streams, concurrent workers, JSON logs and [metrics/SLOs](slo.md). Historical roadmap items for these capabilities are now implemented; live marketplace integration and production HA remain future work.

[Documentation index](README.md) · [中文项目说明](../README.zh-CN.md)

The running application exposes [OpenAPI JSON](http://127.0.0.1:8000/openapi.json) and [interactive API documentation](http://127.0.0.1:8000/docs). These schemas are the field-level reference. Use the host port configured for your deployment.

## Inspection API

| Method and path | Behavior |
|---|---|
| GET `/`, GET `/admin` | Inspection workbench and administrator console |
| GET `/health` | MySQL SELECT 1; HTTP 503 if unavailable; reports Elasticsearch availability separately |
| POST `/api/products/inspect?mode=rules` | One product JSON object; mode is rules/full, default full |
| GET `/api/tasks/{task_id}` | Execution status, product ID and failure information |
| GET `/api/results/{task_id}` | Persisted structured report |
| GET `/api/tasks/{task_id}/traces` | Ordered execution trace |
| GET `/api/results/{task_id}/export` | Report and trace JSON download |
| GET `/api/rules` | Filter by category, issue_type, query and top_k |
| POST `/api/evaluations/run?mode=rules` | Administrator + CSRF required; synchronously runs the fixed 50 cases |
| GET `/api/evaluations/latest` | Latest generated evaluation report |

The single-product endpoint accepts one object, not the entire fixture array. The evaluator iterates all cases. Administrator batch inspection and durable jobs accept their own items envelopes.

Input includes `product_id` (1–128 characters), `title` (1–500), `description` (up to 20,000 characters and 60,000 UTF-8 bytes), `attributes` and optional `category`. Category values are 食品, 美妆 and 3C, regardless of UI language. Missing category uses the model in full mode when available, then deterministic inference; unresolved input returns 422.

Reports include task/status/category, risk/score/issues, evidence and rule references, suggested copy, summary, warnings, degradation and model-use metadata. `success` means execution completed, not that the copy passed. `partial` means required checks failed and the score is provisional. `model_used` indicates at least one successful model call, not complete model coverage. See [architecture](architecture.md) for scoring.

Input errors return 422; missing records 404; authorization/CSRF failures 401/403; state conflicts and concurrent evaluation 409. Evaluation locking is process-local. The public single-product demo endpoint remains anonymous; administrator-originated task/report/trace/export reads require login even through the older URLs.

## Administrator API

The [administrator guide](admin.md#接口与认证) lists catalog, simulation, jobs, publication and authentication routes. Every administrator write except login requires a valid session and `X-CSRF-Token`; cookies are HttpOnly. Login returns the token, and `GET /api/admin/auth/me` restores it after refresh.

Content updates and decisions carry `expected_version`. Single-product `publish` also requires `expected_inspection_id` from the product's current, reviewed `latest_inspection_id` (the numeric product_inspections ID, not task_id). Although optional in the shared action schema, this field is mandatory for publication at runtime; missing or changed evidence returns 409. Other status actions do not require it.

Batch publication first calls `POST /api/admin/products/batch-publish/preview`, then submits selected snapshots containing `id`, `expected_version`, `expected_status` and `expected_inspection_id` plus a nonblank review reason to `POST /api/admin/products/batch-publish`. At most 20 distinct items are accepted. Each item is checked and committed separately; a failed item does not roll back already-published items.

## MySQL tables

There are 13 ORM tables; associations are described below. The ORM and service transaction checks define the actual constraints, rather than this table implying a foreign key on every association.

| Table | Stored data and association |
|---|---|
| `product_samples` | 50 seeded sample inputs; unique product_id; API submissions do not become fixtures |
| `quality_rules` | Rule text, category, examples, suggestions, version and status; unique rule_id |
| `inspection_tasks` | Task ID, source, state, timestamps, full input_json snapshot, input_hash and optional idempotency_key |
| `inspection_results` | Issues, risk score, suggestions and full report_json; unique task_id |
| `agent_traces` | Step/Skill/Tool summaries, status and latency; many per task_id |
| `evaluation_cases` | Fixed product/gold, latest predicted issues and per-case metrics; unique case_id |
| `managed_products` | Current merchant/product fields, content version, publication status and current inspection pointer |
| `product_revisions` | Historical complete product input and editor for each content version |
| `product_inspections` | Managed product/version, task association, report snapshot, risk and execution state |
| `product_audits` | Creation, edits, checks and review decisions with actor, reason and state changes |
| `admin_sessions` | SHA-256 session-token digests, identity and expiry; no plaintext session token |
| `inspection_jobs` | Durable batch identity, idempotency key/payload hash, state, lease and cancellation |
| `inspection_job_items` | Product/version items, persistent task IDs, outcomes and retry attempts |

For inspected API copy, original input is in `inspection_tasks.input_json` and findings are in `inspection_results`. Legacy tasks created before input snapshots were introduced may have null input fields. For managed copy, current/historical text is in `managed_products`/`product_revisions`, with report associations in `product_inspections`. Repeated ordinary inspect POSTs create separate tasks even when product_id is unchanged.

Evaluation gold risk is stored inside `evaluation_cases.product_json`, while expected issues have a separate column. Gold fields are removed before input/model calls. Each case stores its latest prediction and metrics; historical inspection tasks remain separate. JSON/Markdown evaluation reports are files, not additional database tables.

## Initialization and upgrades

ORM definitions are in `db/models.py`, `db/catalog_models.py`, `db/auth_models.py` and `db/job_models.py`. Initial SQL is in `scripts/init_db.sql`, `scripts/init_catalog.sql` and `scripts/init_auth.sql`; Compose mounts the first script on an empty MySQL volume. Application startup imports models and creates missing tables, including queue tables.

`create_all` creates missing tables but does not alter existing columns. `db/migrations.py` adds specifically supported missing columns, including task input snapshots and job payload hashes. Run `python -m scripts.migrate` after backing up an existing deployment; startup also applies these upgrades. This is an additive compatibility hook, not Alembic, a migration ledger or automated rollback.

Normal startup inserts missing sample/rule records, preserves existing rule edits and refreshes fixture gold. Explicit `python -m scripts.seed_data` also overwrites matching fixture rule IDs. An inserted count of zero means no new records were inserted, not an empty database. Catalog seeding inserts 12 missing demo products without overwriting edited products.

MySQL is the rule-maintenance source. After intentional rule updates, `python -m scripts.index_rules` builds a content-versioned Elasticsearch index and switches the configured alias. Disable a rule in MySQL and reindex to remove it from retrieval. See [configuration and backups](configuration.md).
