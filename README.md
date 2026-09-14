# E-commerce Copy Quality Inspection Agent

[English](README.md) · [简体中文](README.zh-CN.md) · [Operations guide](docs/platform-operations.md)

**LLM-led e-commerce content review with rule-based safeguards and auditable publication decisions.**

A runnable engineering project with a bilingual administrator console, DeepSeek semantic review, database-per-tenant isolation, Redis Streams, concurrent workers, and observable review workflows. Policy checks and sample copy are primarily Chinese; changing the interface language does not translate or alter evidence.

![System architecture](docs/assets/architecture.svg)

## What the project does

- Manage merchant submissions, immutable revisions, inspection reports and manual publication decisions.
- Let the LLM inspect original copy against applicable policy first; validate every quoted evidence span and rule reference, then add deterministic safeguards the model cannot override.
- Isolate each tenant's products, reports, jobs, sessions and evaluation outputs in a dedicated MySQL schema with its own database account.
- Accept background jobs with HTTP 202; use Redis Streams for delivery and MySQL for durable item state, fenced leases, retry backoff and recovery.
- Bound worker concurrency and tenant queue capacity; enforce a distributed model concurrency and request budget.
- Export Prometheus metrics, preconfigured Grafana dashboards, Alertmanager rules and JSON runtime logs.
- Generate 10,000+ reproducible synthetic submissions with configurable tenants, concurrency and ingress rate.

This is a tested development platform, not a claim of certified compliance, perfect model accuracy or production SLO attainment. Publication changes this application's catalog status; live marketplace adapters, SSO/RBAC, provider-specific callbacks and high-availability infrastructure remain deployment work.

## Start locally

Requires Docker Desktop / Docker Compose v2. Reserve roughly 6 GB RAM if enabling the optional monitoring stack.

```bash
test -f .env || cp .env.example .env
mkdir -p .local/runtime
docker compose up --build -d --wait
docker compose ps
```

| Entry | Address / local default |
|---|---|
| Admin console | http://127.0.0.1:8000/admin |
| Inspection workbench | http://127.0.0.1:8000/ |
| API reference | http://127.0.0.1:8000/docs |
| Demo login | Tenant `default`, username `admin`, password `admin12345` |
| MySQL | Host port `3307`, container port `3306` |
| Redis | Host port `6380`, container port `6379` |

Use real credentials before sharing a deployment. Add `DEEPSEEK_API_KEY` and your available `DEEPSEEK_MODEL` to the ignored local `.env`; no model key is distributed with this repository. Full mode is the default in the admin UI. Without a model, full mode produces an explicitly degraded report that cannot authorize publication. Explicit rules mode remains available for offline baselines and inexpensive load tests; it never means a model reviewed the product.

All `/api/` data routes now require a tenant session; mutating requests also require its CSRF token. Log in through the admin page before using the workbench. Anonymous access to old task IDs is intentionally no longer supported.

## Try the workflow

1. Log in and choose Chinese or English.
2. Generate a small synthetic preview, or create a product with title, description, category and attributes.
3. Select products and submit batch inspection. The browser polls a durable job while independent items run concurrently.
4. Review source evidence, policy references, risk, warnings and the execution trace.
5. Edit and reinspect if needed. Review eligible current reports, enter a review note and publish.
6. Inspect revision and audit history. Stale, high-risk or incomplete reports are blocked; model failure cannot silently become a full-mode pass.

The Agent is a custom Python State / Skill / Tool orchestrator, with explicit dependencies and validated output. It does not use LangChain, LangGraph, autonomous tool selection or foundation-model training.

## Multi-tenant operation

An operator provisions a tenant using `python -m scripts.provision_tenant <slug>`, with `MYSQL_ADMIN_URL` and `TENANT_ADMIN_PASSWORD` provided securely in the environment. This creates a dedicated schema and database user, hashes the administrator password and registers the connection in ignored local runtime configuration. API and worker containers receive no MySQL root credential.

Log in using the tenant slug. The cookie prefix chooses a registered database but grants no authority: its random bearer token must exist and be unexpired in that database. Caller-supplied tenant headers and URLs cannot override an authenticated database binding. See the [provisioning and upgrade instructions](docs/platform-operations.md).

## Generate 10,000+ products

Offline export needs neither MySQL nor a model:

```bash
python -m scripts.generate_load --count 12000 --tenants default,merchant-east,merchant-west \
  --delivery export --output simulation-output/load.jsonl
```

For registered tenants, an operator can import through the same versioned catalog service:

```bash
python -m scripts.generate_load --count 12000 --tenants default,merchant-east,merchant-west \
  --delivery database --concurrency 4 --requests-per-second 4 --batch-id scale-demo
```

Use `--delivery api --clients-file .local/load-clients.json` to exercise authenticated HTTP ingress. Add `--inspect` to enqueue a rules-only baseline. Each delivery request stays at or below 100 products; the CLI bounds concurrent requests and backs off on HTTP 429. Same seed/batch/chunk plan safely resumes unchanged products; changing the plan requires a new batch ID.

The generator marks provenance and scenarios, never fabricates reports, overwrites edited products, publishes automatically or changes the fixed evaluation labels. The 50 authored evaluation cases are separate from generated load and are not independently expert-validated.

## Monitor

```bash
docker compose -f compose.yaml -f compose.monitoring.yaml up -d --build
```

- Grafana: http://127.0.0.1:3000 — local `admin / local-grafana-change-me`; configure `GRAFANA_ADMIN_PASSWORD`.
- Prometheus: http://127.0.0.1:9090
- Alertmanager: http://127.0.0.1:9093
- Metrics: API `/metrics`, worker port `9101` on the internal Docker network.

Alerts initially deliver to a local JSON log receiver, not an email or paging service. [SLO definitions and runbooks](docs/slo.md) explain denominators, windows, exclusions and limits.

## Verify

```bash
python -m pip install -r requirements-dev.txt
DEEPSEEK_API_KEY='' python -m pytest -q
npm ci --ignore-scripts
npm test
```

Use a separate test MySQL database with `RUN_MYSQL_TESTS=1`. Supplying a test `MYSQL_ADMIN_URL` additionally tests independent schema provisioning and SQL privilege denial; Redis is needed for transport recovery tests. Never aim test provisioning at a production instance. CI runs MySQL, Redis, Python and browser tests without a model key.

Security and concurrency tests include cross-tenant task/report/job access, cookie-prefix forgery, simultaneous tenant reads, independent item leases, stale-owner fencing, backoff, Redis stream loss/rebuild, LLM evidence validation and publication version guards.

## Documentation and scope

- [Platform deployment, tenancy, concurrency and secrets](docs/platform-operations.md)
- [Metrics, alerting, SLOs and operational limits](docs/slo.md)
- [Local validation results](docs/platform-validation.md)
- [Simulation scenarios](docs/data-simulation.md)
- [Existing API and database reference](docs/api_database.md)

Each tenant owns the same 13 business tables: samples/rules/evaluation fixtures; tasks/results/traces; products/revisions/inspections/audits; sessions; jobs/items. The registry is operator configuration, not a user-controlled tenancy column.

Redis delivery is at least once. Business writes are guarded by SQL leases, durable execution IDs and product versions; external LLM execution is not promised exactly once. The Compose deployment is single-host and does not demonstrate failover, backups across sites or sustained production capacity.

MIT licensed. No private databases, model keys, operator credentials or internal project plans belong in Git.
