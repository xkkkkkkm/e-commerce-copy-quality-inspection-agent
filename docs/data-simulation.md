# Synthetic merchant submissions / 模拟商家商品接入

This generator supplies reproducible **synthetic** merchant submissions for the administrator workflow. It does not scrape a marketplace, use platform credentials, impersonate real merchants, or modify the fixed evaluation fixtures/gold labels. Nine clearly labelled simulated merchants cover 食品, 美妆 and 3C.

Each submission is accepted through `ProductCreate` and `services.catalog.create_product`: its initial state is `pending`, content version is 1, and the normal revision and creation audit are recorded. Delivery never generates a pass result, inspection history or publication. Optional inspection queues the existing durable worker in **rules** mode; workers produce actual results and normal publication guards continue to apply.

## Offline preview and JSON export

```bash
.venv/bin/python -m scripts.simulate_ingestion --count 30 --seed 42 --batch-id demo-42
.venv/bin/python -m scripts.simulate_ingestion --count 30 --seed 42 --batch-id demo-42 > /tmp/synthetic-demo-42.json
.venv/bin/python -m scripts.simulate_ingestion --help
```

Generation needs only the installed Python requirements. It does not import the database engine, read a `.env` file, call an API, or require an LLM key. stdout is a JSON envelope containing `batch_id`, `seed`, `count`, `source: "synthetic"`, `scenario`, `generator_version`, `products` and a category/scenario/merchant summary. Each product entry has a `product` conforming to `ProductCreate` and a separate `provenance` object.

The same provenance is retained in the reserved `product.attributes._simulation` object: `source`, `generator_version`, `batch_id`, `seed`, per-product `scenario`, `requested_scenario`, `batch_count` and `ordinal`. Metadata includes no risky claim examples or expected inspection labels. The scenario describes intentionally generated input, not a completed assessment.

| Scenario | Submitted content |
| --- | --- |
| `mixed` (default) | Rotates clean, risky, missing and conflict across all three categories |
| `clean` | Complete attributes and conservative, internally consistent copy |
| `risky` | Deliberate category-specific promotional or medical claims |
| `missing` | Removes one or two required attributes |
| `conflict` | An explicit text claim contradicts a structured attribute |

`count` is a strict integer from 1–100. `seed` is a strict integer from 0–4294967295. `batch_id` is 1–64 ASCII letters, digits, hyphens or underscores and must start with a letter or digit. Unknown fields are rejected.

## Authenticated delivery

Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` in the environment or project `.env`, then run:

```bash
.venv/bin/python -m scripts.simulate_ingestion --count 30 --seed 42 --batch-id demo-42 --deliver --base-url http://127.0.0.1:8000
.venv/bin/python -m scripts.simulate_ingestion --count 30 --seed 42 --batch-id demo-42 --deliver --no-inspect
```

Credentials are not command-line arguments or output. The CLI obtains an administrator session, submits its CSRF token, delivers one bounded batch and logs out. With the default setting, a worker must be running to consume the returned `job_id`; enqueueing itself does not mean the products have passed inspection. `--no-inspect` leaves products pending without creating a job. The CLI intentionally submits one stable batch rather than changing batch identity through per-item requests.

Both API endpoints require administrator authentication and `X-CSRF-Token`, including preview:

- `POST /api/admin/simulation/preview`: `{ "count": 30, "seed": 42, "scenario": "mixed", "batch_id": "demo-42" }`
- `POST /api/admin/simulation/deliver`: the same body plus optional `"enqueue_inspection": true` (default).

Delivery returns `{ "batch_id": "demo-42", "created_count": 30, "existing_count": 0, "job_id": "job_…", "products": [{ "id": 123, "product_id": "sim_…_001", "version": 1 }], "source": "synthetic" }`. `job_id` is `null` when inspection is disabled. The queue accepts the entire batch of up to 100 products; the synchronous 20-item inspection route is not used.

## Reproducibility and conflicts

The generator uses a local per-product PRNG and no clock. With the same generator version and request, JSON payloads, product IDs and ordering are identical. IDs derive from seed, batch ID and ordinal, so changing scenario, count or generator content under the same identity cannot silently overwrite records. Choose a new batch ID for changed input.

An identical delivery reuses matching version-1 products and the existing inspection job. The service compares all submitted fields, including provenance, before reuse. Changed payloads and edited products return HTTP 409, even if a user later edits the content back to its original text. Existing status and inspection history are preserved. Preflight detects conflicts among existing records before creating new ones.

Creation uses the existing domain service's per-product commits. A connection failure or concurrent conflict may therefore leave part of a batch created; retrying the **identical** request resumes safely and can recover queue creation. The feature does not promise an all-or-nothing batch transaction. No automatic cleanup or publication follows a failed request.

## Verification

Follow the [isolated MySQL setup](testing.md#mysql-integration-tests) before enabling the MySQL tests. Use its test environment block and select the simulation test file if only this feature needs verification.

```bash
.venv/bin/pytest -q tests/test_simulation.py
RUN_MYSQL_TESTS=1 .venv/bin/pytest -q tests/test_simulation_mysql.py
```

Offline tests verify determinism, strict bounds, category/scenario variety, provenance isolation and real deterministic rule behavior without LLM calls. Simulation MySQL tests create unique batch identities and clean up only their own generated products, history, sessions and jobs. Their queue test cancels its job inside the insert transaction. The wider job test suite does claim jobs, so run the full integration suite only in the isolated test instance.
