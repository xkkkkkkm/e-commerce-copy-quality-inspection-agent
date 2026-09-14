# Product Copy Quality Agent

[English](README.md) | [简体中文](README.zh-CN.md) | [Documentation](docs/README.md)

A reproducible software engineering project for reviewing **Chinese e-commerce product copy**, with a bilingual administrator console, deterministic quality checks, retrieval of supporting rules, optional DeepSeek semantic analysis, and a MySQL-backed review workflow.

The repository is a portfolio/research prototype. The interface is bilingual, but submitted copy and deterministic policy checks remain Chinese-focused.

![Architecture overview](docs/assets/architecture.svg)

The project demonstrates backend architecture, database transactions, conservative LLM integration, asynchronous job execution, evaluation, and deployment. It uses a custom Python orchestrator; it does not depend on LangChain, LangGraph or model-driven tool calling, and it does not train a foundation model.

## Run the demo

Prerequisites: Docker Desktop with Docker Compose v2. Allow at least 4 GB of memory for the local stack. Run these commands in the cloned repository directory:

```bash
test -f .env || cp .env.example .env
docker compose up --build -d --wait
docker compose ps
curl http://127.0.0.1:8000/health
```

If `.env` already exists, keep the existing file. For the local demonstration, open:

| Entry point | URL / credentials |
|---|---|
| Administrator console | <http://127.0.0.1:8000/admin> |
| Product inspection workbench | <http://127.0.0.1:8000/> |
| API reference | <http://127.0.0.1:8000/docs> |
| Local demo account | `admin` / `admin12345` |
| MySQL Workbench | `127.0.0.1:3307`; database/user/password: `quality_agent` |

The application, MySQL and Elasticsearch ports bind to localhost. The MySQL container uses port 3306 internally. Credentials above are development defaults; configure your own before sharing a deployment. Do not commit `.env`.

Startup creates missing tables, applies the supported column upgrades and seeds missing demo records. The default Compose stack starts MySQL, Elasticsearch, the API and a separate worker. It waits for Elasticsearch health even though runtime retrieval supports a fallback. Stop it with `docker compose down`; named volumes preserve the database and evaluation reports. Avoid `down -v` when you want to keep this data.

Choose **English** in the language selector. The preference persists between the console and workbench and across refreshes. Interface labels, status messages and the supplied rule explanations are localized. Product copy, quoted evidence, custom rule text, reviewer notes and downloaded source reports retain their original language. Switching the interface does not translate submitted copy or change the detector's supported language.

No API key is required for rule inspection, local retrieval, sample generation or the administrator workflow.

## Suggested reviewer walkthrough

1. Sign in, choose English, and inspect the existing catalog and version histories.
2. Open **Simulate data**. Choose a seed, a batch identifier and a scenario. Preview generated records before delivering them to the catalog.
3. Deliver the batch with rule inspection enabled. The worker processes a durable MySQL job; refresh the job status to see per-product progress. Newly delivered products are pending review.
4. Open a product report: inspect the issue type, source evidence, applicable rule, score and execution trace.
5. Edit a product. Saving creates a new revision and invalidates the earlier inspection. Run inspection again before attempting publication.
6. Review a complete report and publish an eligible product with a review note. High-risk, stale or incomplete reports cannot authorize publication. Publication changes this application's catalog status only.

For batch publication, select up to 20 products and choose **Review and publish batch**, or **Continue to publication review** after batch inspection. The preview shows eligible and blocked products with their current reports. Passed products are preselected; low- and medium-risk products require manual selection after reviewing their issues. Enter a review note and confirm before publishing. Each item uses an independent transaction, rechecks its version, status and report under a row lock, and records an audit entry. Results distinguish published, failed and unsubmitted products. After an interrupted response, check status again before retrying; publication is not an automatically retried background job.

## Architecture

```text
Bilingual browser UI (plain HTML / CSS / JavaScript)
    → FastAPI: input validation, administrator session + CSRF
    → Catalog service: product revisions, review decisions, audit records
    → MySQL inspection jobs → separate worker
    → Python Agent: category → rule retrieval → checks → score → safe rewrite → report
        ├─ Deterministic Python tools and category skills
        ├─ Elasticsearch keyword search; MySQL / bundled JSON fallback
        └─ Optional DeepSeek category / semantic analysis
    → MySQL: input, report, trace and catalog inspection association
```

The interactive single-product endpoint and legacy small-batch endpoint remain synchronous HTTP operations. The `/api/admin/jobs` endpoints provide durable background execution. The worker uses leases, heartbeat renewal, bounded retry attempts and catalog version checks. The stack is intended for a single-machine demonstration, not an independently benchmarked high-availability service.

| Layer | Implementation |
|---|---|
| API and schemas | FastAPI, Pydantic |
| Persistence | MySQL 8, SQLAlchemy, PyMySQL |
| Retrieval | Elasticsearch 8.17, keyword search with category filtering |
| Agent | Custom Python state and Skill/Tool orchestration |
| Optional model | DeepSeek chat completions, strict structured output validation |
| UI | Plain JavaScript and explicit Chinese/English message dictionaries |
| Deployment and testing | Docker Compose, pytest, Node.js language-layer tests |

## Data sources and simulation

This repository does **not** contain scraped merchant data or a live marketplace feed. Its supplied data is synthetic:

- `data/samples/products.json`: 30 manually authored product examples.
- `data/evaluation/cases.json`: 50 fixed evaluation examples, including the original 30 and 20 additional cases. Expected labels were authored for the project; they have not been independently validated by domain experts.
- `scripts/seed_catalog.py`: imports 12 products from the sample set for the initial management demo.
- `services/simulation.py`: produces configurable, deterministic batches for exercising the submission and inspection workflow. Scenarios include clean descriptions, unsupported claims, missing attributes and contradictions. Scenario names describe how inputs were constructed; they are **not** asserted inspection outcomes or evaluation gold.

Generated products carry a `_simulation` attribute recording provenance, generator version, seed, batch and scenario. Delivery uses the catalog service and preserves version/audit records. Repeating the same batch reuses matching records; a conflicting or edited record is rejected instead of being overwritten. Optional delivery to the inspection queue performs real rule checks; the generator never invents reports or publishes products automatically.

Generate a reproducible JSON preview without the database or a model:

```bash
docker compose exec -T api python -m scripts.simulate_ingestion --count 30 --seed 42 --batch-id demo-42 --scenario mixed
```

Use the administrator's **Simulate data** dialog to preview and submit a batch, or consult the [simulation guide](docs/data-simulation.md) and CLI `--help` for authenticated API delivery. Generated traffic is a functional simulation, not a production load benchmark. It does not update the fixed evaluation labels.

## Example input and output

```bash
curl -s 'http://127.0.0.1:8000/api/products/inspect?mode=rules' \
  -H 'Content-Type: application/json' \
  --data-binary @data/samples/demo_food.json
```

Inputs contain `product_id`, `category`, `title`, `description` and `attributes`. API category values remain `食品` (food), `美妆` (beauty), and `3C` (electronics), independent of the interface language. The report includes `task_id`, status, risk level, issues, evidence, rule references, conservative copy suggestions, warnings, model-use flags and rule-source metadata. Use the returned `task_id` to retrieve its report and trace; administrator-originated reports require authentication.

## Database responsibilities

| Tables | Purpose |
|---|---|
| `product_samples`, `evaluation_cases` | Fixed sample inputs, authored expected labels, evaluation predictions and metrics |
| `quality_rules` | Authoritative rule text, examples, status and version |
| `inspection_tasks`, `inspection_results`, `agent_traces` | Full submitted input snapshots and hashes, execution tasks, persisted reports and trace steps |
| `managed_products`, `product_revisions` | Current catalog content and immutable historical input revisions |
| `product_inspections`, `product_audits` | Version-associated inspection snapshots and reviewer actions |
| `inspection_jobs`, `inspection_job_items` | Durable batches, idempotency hashes, leases, progress and retries |
| `admin_sessions` | Hashed administrator session tokens and expiry |

Normal startup fills missing demo rules without overwriting existing MySQL rule edits. Explicit fixture import (`python -m scripts.seed_data`) can overwrite matching fixture rule IDs. Run `python -m scripts.index_rules` after intentional rule updates to build a versioned Elasticsearch index and switch its alias. Rule text provides evidence and semantic guidance; adding a rule record does not automatically implement a new deterministic Python check.

## Optional LLM configuration

Set the following in your local `.env`, then recreate both application processes:

```dotenv
DEEPSEEK_API_KEY=your_key_here
DEEPSEEK_MODEL=deepseek-chat
```

```bash
docker compose up -d --force-recreate api worker
```

Use full mode for semantic analysis. If the model is unavailable or returns invalid JSON or unsupported citations, the system retains rule findings and marks coverage as limited. A full-mode result without successful model use is not evidence of LLM quality. Copy and summary echo-generation calls are disabled by default; deterministic suggestions avoid introducing unsupported product facts.

Local Elasticsearch keyword retrieval does not require an embedding API or an API key. Vector embeddings, reranking and English-language policy enforcement have not been implemented.

## Validation

Use Python 3.12 and Node.js 22 or newer for the documented development setup. Create a virtual environment and install the test dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
RUN_MYSQL_TESTS=0 .venv/bin/pytest -q
npm ci --ignore-scripts
npm test
```

MySQL integration tests are opt-in. Follow the [isolated MySQL test setup](docs/testing.md#mysql-integration-tests) before enabling `RUN_MYSQL_TESTS=1`. Queue tests claim jobs in their target database, so use a separate test instance with no application worker attached.

Run the fixed 50-case rule baseline without external services:

```bash
.venv/bin/python evaluator/run_eval.py
```

Evaluation distinguishes issue recall, false discoveries, risk classification and model usage. Passing engineering tests does not imply perfect detection. See the [test guide](docs/testing.md), [evaluation guide](docs/evaluation.md) and [dataset notes](data/evaluation/README.md) for reproducible commands and label limitations. No validated real-model quality result is claimed without an actual keyed model evaluation. A GitHub Actions workflow is included; its presence is not a claim that a remote CI run has passed.

## Scope and limitations

See [current scope](docs/scope.md) for implemented capabilities and exclusions. After upgrading an existing deployment, refresh any already-open pages to load the updated interface.

This is a portfolio/research prototype and an internal review aid. It is not legal advice or a certified compliance engine. It does not connect to marketplace listing APIs, inspect images, manage orders or provide merchant accounts and multi-role permissions. Rules and labels require expert review before operational use. The system is not claimed to support reliable inspection of English product copy simply because its UI can be displayed in English.

Docker volumes preserve state across restarts; they are not backups. Back up MySQL separately before deployment upgrades. API keys and database exports should remain outside the public repository. See [configuration and operations](docs/configuration.md) for local development, credentials, backups and troubleshooting, and the [documentation index](docs/README.md) for architecture and database details.
