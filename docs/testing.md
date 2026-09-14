# Testing and verification

[Documentation index](README.md) · [中文项目说明](../README.zh-CN.md)

Run commands from the repository root. Python 3.12 matches Docker and CI; Node.js 22 or newer is needed for JavaScript tests. Node dependencies are development-only: the served UI has no build step.

## Offline checks

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
RUN_MYSQL_TESTS=0 .venv/bin/pytest -q
npm ci --ignore-scripts
npm test
.venv/bin/python evaluator/run_eval.py --mode rules
```

The default Python run skips opt-in MySQL tests. Model tests use controlled responses; no real API key is needed. The evaluator independently runs 50 fixed examples with bundled rules, writes ignored files under `evaluator/reports/` and needs no database, Elasticsearch or model.

## MySQL integration tests

Use an isolated test instance with no application worker attached. Queue tests claim jobs in their configured database; using the everyday catalog database can interfere with real pending jobs. The following test container is independent of `docker compose` and uses host port 13307:

```bash
docker run --detach --rm --name quality-agent-test-mysql \
  --publish 127.0.0.1:13307:3306 \
  --env MYSQL_DATABASE=quality_agent_test \
  --env MYSQL_USER=quality_agent \
  --env MYSQL_PASSWORD=test_password \
  --env MYSQL_ROOT_PASSWORD=test_root_password \
  --health-cmd='mysqladmin ping --silent' \
  --health-interval=5s --health-timeout=5s --health-retries=30 \
  mysql:8.0

docker inspect --format '{{.State.Health.Status}}' quality-agent-test-mysql
```

Repeat the inspect command until it returns `healthy`. If it remains unhealthy, inspect `docker logs --tail=80 quality-agent-test-mysql`. Do not continue to the next block before MySQL is ready. If the container name or port is already in use, identify the existing test instance or choose a different test name/port and update the commands.

Run this block in Bash or Zsh. The subshell keeps test settings out of subsequent application commands; a failed setup step stops the block.

```bash
(
  set -e
  export DATABASE_URL='mysql+pymysql://quality_agent:test_password@127.0.0.1:13307/quality_agent_test?charset=utf8mb4'
  export ELASTICSEARCH_URL='http://127.0.0.1:19200'
  export DEEPSEEK_API_KEY=''
  export APP_ENV=local
  export RUN_MYSQL_TESTS=1
  .venv/bin/python -c "from app.main import app; from db.session import Base, engine; Base.metadata.create_all(engine)"
  .venv/bin/python -m scripts.migrate
  .venv/bin/python -m scripts.seed_data
  .venv/bin/pytest -q
)
```

Importing the application registers all ORM models before creating tables. The explicit seed import is appropriate for this isolated fixture database. Elasticsearch is not required for this integration suite. Do not start `scripts.worker` against this database while tests are running.

After reviewing test results, the following stops and removes only the disposable test container and its temporary data (because it was started with `--rm`):

```bash
docker stop quality-agent-test-mysql
```

中文提示：上面的 13307 是独立测试 MySQL；日常项目仍使用 3307。测试配置只在括号内生效。不要把测试库地址改成正在使用的商品数据库，也不要用 `docker compose down -v` 清理测试。

## HTTP workflow checks

With the normal demo stack running:

```bash
docker compose exec -T api python -m scripts.verify_project --base-url http://127.0.0.1:8000
docker compose exec -T api python evaluator/run_eval.py --api-url http://127.0.0.1:8000 --mode rules --persist
docker compose cp api:/app/evaluator/reports ./output-evaluation
```

The first command writes three verification tasks. The second submits all 50 cases through the authenticated single-product API, creating task/report/trace records, and additionally updates evaluation predictions. Both CLIs now log in using `TENANT_ID`, `ADMIN_USERNAME` and `ADMIN_PASSWORD`; use matching tenant context for any local persistence. These commands intentionally write data. See [current platform validation](platform-validation.md) for the latest tests.

For manual UI checks: sign in, switch Chinese/English, simulate a batch, inspect products, edit one to invalidate its report, and preview batch publication. Verify that stale/high-risk/incomplete items are blocked, low/medium risks require review, and eligible publication records the note. Use dedicated demo products: confirmed publication changes their catalog status.

## Coverage and evidence

| Tests | Behavior checked |
|---|---|
| `test_quality*.py`, `test_week2_agent.py`, `test_full_pipeline.py` | Deterministic checks, Agent sequencing, conservative copy and failure handling |
| `test_retrieval_llm.py`, `test_evaluator.py` | Retrieval/model validation, metric denominators and gold isolation |
| `test_admin_auth.py`, `test_admin_api_mysql.py`, `test_catalog.py`, `test_api_mysql.py` | Sessions, CSRF, persistence, versions and publication evidence |
| `test_jobs_mysql.py` | Claiming, leases, cancellation, retries and concurrency guards |
| `test_simulation.py`, `test_simulation_mysql.py` | Deterministic provenance, delivery conflicts and real rule inspection |
| `test_i18n.cjs`, `test_select_controls.cjs`, `test_batch_publication.cjs` | Translation, form interactions and batch-publication UI state |

The local engineering run on 2026-09-11 passed **181 Python tests** with MySQL integration enabled and **24 Node tests**. That Python run used 3.13; Docker and the included CI workflow target 3.12. Counts describe that dated run, not a guarantee for every environment. Browser layout and keyboard behavior also need manual checks; jsdom is not a rendered-browser test.

[GitHub Actions](../.github/workflows/ci.yml) provisions a separate MySQL 8 database and runs Python, Node, script-syntax and Compose checks. No hosted CI result is claimed before GitHub executes the workflow. These checks do not benchmark HA, production load, visual quality, real Elasticsearch integration or keyed LLM quality.

Evaluation is a separate measurement of detection on project-authored synthetic labels. It reports errors and exact issue-label mismatches rather than enforcing a perfect score. Full mode without successful model use is not evidence of LLM quality. See [evaluation methodology](evaluation.md) and [dataset limitations](../data/evaluation/README.md).
