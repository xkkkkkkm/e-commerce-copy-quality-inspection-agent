# Configuration and operations

> Version 2 update: the [current platform guide](platform-operations.md) supersedes earlier single-tenant/public-API examples in this document. Current behavior uses LLM-led full-mode review, database-per-tenant isolation, authenticated data routes, Redis Streams, concurrent workers, JSON logs and [metrics/SLOs](slo.md). Historical roadmap items for these capabilities are now implemented; live marketplace integration and production HA remain future work.

[Documentation index](README.md) · [中文项目说明](../README.zh-CN.md)

## Environment

Create a local `.env` from [.env.example](../.env.example). Existing shell variables take precedence over dotenv in local Python processes. Compose reads `.env` and sets container database/search hostnames explicitly. Never commit your real `.env`.

| Setting | Demo default | Meaning |
|---|---|---|
| DATABASE_URL | MySQL/PyMySQL on 127.0.0.1:3307/quality_agent | Local Python connection; only mysql+pymysql is supported |
| MYSQL_DATABASE / MYSQL_USER / MYSQL_PASSWORD | quality_agent | Initial MySQL database and application credentials |
| MYSQL_ROOT_PASSWORD | change_me | Initial root password and health-check credential |
| MYSQL_PORT | 3307 | Host port; the container uses 3306 |
| ELASTICSEARCH_URL | http://127.0.0.1:9200 | Local search address; Compose uses http://elasticsearch:9200 |
| ELASTICSEARCH_PORT | 9200 | Host search port |
| ELASTICSEARCH_INDEX | quality_rules | Rule index/alias |
| ELASTICSEARCH_TIMEOUT_SECONDS | 2 | Per-request search timeout, 0.1–30 seconds |
| DEEPSEEK_API_KEY | empty | Optional model key; empty disables model calls |
| DEEPSEEK_BASE_URL | https://api.deepseek.com | Client appends /chat/completions |
| DEEPSEEK_MODEL | deepseek-chat | Model identifier |
| DEEPSEEK_TIMEOUT_SECONDS | 20 | Per-request model timeout, 1–60 seconds |
| DEEPSEEK_MAX_RETRIES | 1 | Additional attempts for connection errors/429/5xx, 0–2 |
| APP_HOST | 0.0.0.0 | Available to custom launchers; explicit Uvicorn arguments take precedence |
| APP_PORT | 8000 | Compose host API port; container always uses 8000 |
| APP_ENV | local | production rejects the bundled administrator password |
| APP_MAX_BODY_BYTES | 262144 | HTTP body size limit |
| ADMIN_USERNAME / ADMIN_PASSWORD | admin / admin12345 | Local demo account |
| ADMIN_SESSION_HOURS | 8 | Session lifetime, minimum one hour |
| ADMIN_COOKIE_SECURE | false | Use true with HTTPS; local HTTP needs false |
| ADMIN_THROTTLE_FILE | unset locally; /tmp file in Compose | Optional shared file for login throttling; falls back to process memory |

The model client also recognizes `DEEPSEEK_ENABLE_OPTIONAL_GENERATIONS=1` for constrained copy/summary echo calls. It defaults to off and is not passed by the bundled Compose configuration. Full mode normally calls the model for missing-category inference and semantic analysis; deterministic copy/summary generation does not need a key.

Search clients support `ELASTICSEARCH_API_KEY` or `ELASTICSEARCH_USERNAME`/`ELASTICSEARCH_PASSWORD` for an external protected cluster. These are not passed by the default Compose file. The bundled Elasticsearch is single-node, unauthenticated and bound to loopback; configuring an external cluster requires updating Compose service dependencies, addresses and credentials too.

## Credentials and model changes

After setting the DeepSeek key for LLM-led full mode in `.env`, recreate both processes:

```bash
docker compose up -d --force-recreate api worker
```

Rules mode needs no model key or embedding service. Full mode can cost money and take longer; inspect warnings and trace/model-use fields when a call fails. Invalid structured output is rejected, not blindly retried.

For administrator configuration changes, recreate the API. Existing sessions are stored in MySQL, so revoke them using authenticated `POST /api/admin/auth/logout-all` with CSRF as part of credential rotation. Changing MySQL environment variables does not change passwords already stored in an existing volume; update the actual MySQL user credentials as well. The host-side DATABASE_URL and Compose MYSQL_* settings must agree; reserved characters in connection URL credentials need appropriate URL encoding.

## Local development

Run database and search in Docker, and Python on the host. If a Docker API/worker already runs, stop those two processes before starting host equivalents:

```bash
test -f .env || cp .env.example .env
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
docker compose stop api worker
docker compose up -d --wait mysql elasticsearch
.venv/bin/python -c "from app.main import app; from db.session import Base, engine; Base.metadata.create_all(engine)"
.venv/bin/python -m scripts.migrate
.venv/bin/python -c "from scripts.seed_data import seed; seed(import_rules=False)"
.venv/bin/python -m scripts.seed_catalog
.venv/bin/python -m scripts.index_rules
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

In a second terminal at the repository root, start the queue consumer with the same environment:

```bash
.venv/bin/python -m scripts.worker
```

This setup uses host ports 3307 and 9200 from `.env.example`. ES indexing failures can be investigated separately; the local API can still use MySQL retrieval. The browser UI serves directly from `app/static/`; Node is needed only for [tests](testing.md).

## Rules, upgrades and backups

MySQL `quality_rules` is the maintenance source. Normal startup fills missing bundled rules without overwriting existing edits. Explicit fixture import overwrites matching fixture rule IDs:

```bash
# Only when intentionally restoring/importing bundled fixture rules:
docker compose exec -T api python -m scripts.seed_data
# Synchronize current MySQL rules to a versioned ES index and switch its alias:
docker compose exec -T api python -m scripts.index_rules
```

Seed also refreshes the project evaluation gold while retaining predictions and historical tasks. These are data writes, not read-only health checks.

Back up an existing database before upgrading. The following command writes a local SQL export; choose a new filename if that backup already exists:

```bash
mkdir -p backups
docker compose exec -T mysql sh -c 'exec mysqldump --single-transaction --no-tablespaces -u root -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' > backups/quality-agent.sql
docker compose exec -T api python -m scripts.migrate
```

Check that the dump command succeeded and test restoration into an isolated instance before relying on it. `backups/` is excluded from Git and Docker builds. Named volumes retain data across ordinary restarts and `docker compose down`; they are not backups. Do not use `down -v` to perform an upgrade.

The migration hook adds specifically supported missing columns. Startup also creates missing tables and applies these additions. It is not a complete migration framework with revision history or automated rollback.

## Health and troubleshooting

```bash
docker compose ps
docker compose logs --tail=80 api
docker compose logs --tail=80 worker
docker compose logs --tail=80 mysql
docker compose logs --tail=80 elasticsearch
curl http://127.0.0.1:8000/health
```

| Symptom | Check |
|---|---|
| Cannot connect to Docker daemon | Start Docker Desktop |
| MySQL connection refused | MySQL health and host 3307 mapping; containers use mysql:3306 |
| ES rules missing/stale | MySQL rule status, then index_rules |
| Full mode used no model | Report warnings, DeepSeek trace entries and both containers' updated configuration |
| Port 8000 occupied | Stop the earlier host API, or change APP_PORT for Compose |
| Queued simulation stays pending | Worker logs and matching API/worker DATABASE_URL |

`/health` requires MySQL and reports Elasticsearch capability separately. Default Compose startup and its API health check require Elasticsearch to be available, even though an already-running API can fall back for retrieval. Selecting only `mysql api` still starts the API's declared search dependency; it is not an ES-free deployment command.

## Shared deployment boundary

The default services bind to localhost and use demonstration credentials. The API container runs as a non-root user. Before sharing a hosted instance, configure unique credentials, HTTPS and secure cookies, and review exposure of the anonymous inspection and latest-evaluation demo routes. Administrator reports are protected, and the evaluation-run endpoint requires authentication/CSRF.

`APP_ENV=production` rejects the demo password; it does not supply multi-role authorization, a global rate limiter, centralized monitoring, HA or external marketplace integration. The bundled image tag is not pinned by digest. Review image/dependency updates and infrastructure controls for your own deployment; the repository makes no production-readiness certification.
