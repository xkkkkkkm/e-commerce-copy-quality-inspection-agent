# Local validation · 2026-09-14

This records measured development results, not a production capacity or accuracy guarantee. The run used macOS Docker Desktop, MySQL 8, Redis 7.4, Elasticsearch 8.17, one API and one worker with four execution threads. Host Python was 3.13; application Docker images use Python 3.12. CI is configured for Python 3.12 and Node 22. No model key is included in the repository.

## Engineering checks

- **196 Python tests passed on both host Python 3.13 (15.03 seconds) and an isolated Python 3.12 application container (13.07 seconds)**, including opt-in real MySQL and Redis integration tests. Test data used dedicated test schemas; tenant fixtures created isolated random schemas and removed only those fixture-owned schemas/users.
- **24 browser JavaScript tests passed**, covering translation state, accessible select controls and publication decisions. The async batch contract was updated while preserving the report-to-publication continuation.
- Prometheus `promtool` validated **7 alert rules** and alert scenarios for service failure, queue age, API budget burn, all-failed completions and no-traffic suppression.
- Python `pip-audit` and npm dependency audits reported **no known vulnerabilities** on this run, after upgrading FastAPI/Starlette, cryptography and python-dotenv. These audits are also CI gates; advisory databases change over time.
- Git diff whitespace validation and secret checks covering Git-eligible files, staged blobs and reachable local history passed. Local environment, tenant credentials, operator files, backups and database exports are ignored and excluded from Docker build context.
- Independent read-only review identified concurrency snapshot, tenant-fault coordination, per-attempt model rate accounting, cross-tenant login throttling and runtime credential permissions issues. These were fixed; targeted re-review found no remaining Critical/Important issue in that scope. This is not an exhaustive security certification.

Meaningful regression coverage includes forged tenant cookie prefixes, forbidden SQL reads across database grants, cross-tenant reports/traces/exports/jobs, simultaneous tenant API reads, LLM-first ordering, model evidence constraints, stale-owner fencing, independent batch-item completion, old REPEATABLE READ snapshot completion, concurrent admission capacity, retry deadlines and Redis transport loss/rebuild. The Redis test deletes only its private test stream, never flushes a shared Redis database.

Release review also corrected admission under a one-connection pool and isolated non-default tenant startup failures. Added integration tests cover these cases, restoration after API restart, additive legacy-schema migration with active lease preservation, and worker recovery during execution / after catalog commit. The crash tests inject `BaseException` at those boundaries; they do not constitute a real container-kill or infrastructure chaos campaign. Interrupted unfinished tasks get a new execution ID, while already committed catalog results are reused. Independent re-review found no remaining Critical/Important findings in the fix scope.

## Data and workload

| Run | Measured result |
|---|---|
| Database delivery, 12,000 synthetic products, 3 tenants, concurrency 4, configured 4 requests/second | 47.71 seconds; 4,000 inserted per tenant |
| Authenticated API replay of 600 existing products | 1.39 seconds; all 600 reused without overwrite; six rules jobs enqueued |
| Initial 600-item rules queue run | 600 successful items, six successful jobs; 97 seconds including a lock-contention retransmission delay |
| After targeted claim-lock optimization: new 600-product API ingress | 2.37 seconds; 200 products per tenant |
| Second 600-item rules queue run | 600 successful items, six successful jobs; 19 seconds from earliest enqueue to latest completion (SQL second precision) |

The queue optimization waits for a short targeted job lock when consuming a known Redis item, instead of treating temporary `SKIP LOCKED` contention as no work and waiting for outbox retransmission. Generic polling claims still use `SKIP LOCKED`.

Final catalog counts after these runs: default tenant **4,328**, merchant-east **4,200**, merchant-west **4,200**; **12,728 total**, comprising 12,600 new generated products and 128 preserved existing products. The original **19 published products** remain published; all newly generated products remain unpublished. Synthetic labels were not imported into evaluation gold. A private pre-upgrade MySQL backup was created before schema migration.

These short local runs do not represent sustained or production QPS, tenant fairness under arbitrary provider latency, or peak traffic on real marketplace payloads. The completed rule cases do not measure LLM accuracy.

## Live model check

The authenticated provider model list returned `deepseek-flash` and `deepseek-v4-pro`; the configured model is `deepseek-flash`. An initial risky-food semantic check completed in approximately **6.59 seconds**, with validated model evidence and no degradation.

Three simultaneous authenticated full-mode API checks across the default/east/west tenants returned HTTP 200, `model_used=true`, `degraded=false`, and high-risk findings. Individual elapsed times were **11.51 / 8.64 / 6.67 seconds**, with **11.62 seconds** total wall time. This verifies a small real concurrent integration path, not general precision/recall or provider throughput.

After release fixes and the dependency upgrade, another live full-mode HTTP check completed in **13.04 seconds**, returned `model_used=true`, `degraded=false`, high risk, and a retrievable persisted report. Main catalog counts remained **12,728**, with the original **19** published products unchanged.

No 10,000-item model run was performed. Bulk data validation used explicit rules mode to avoid unbounded model expense. No generated product was automatically published.

A separate 10,000-record, three-tenant JSONL export was generated twice using the same seed and batch ID. Both outputs contained exactly 10,000 records and were byte-identical. This repeatability check added no rows to the main catalog and made no model calls.

## Deployment and visibility

API, MySQL, Redis and Elasticsearch passed their configured health checks; worker and monitoring services were running. Prometheus API and worker targets were `up`; it observed overlapping worker activity. The runtime tenant registry inside the serving container was owned by service UID 100 with permission **0600**, and the process ran as UID 100.

A synthetic `LocalDeliveryVerification` alert submitted to local Alertmanager returned HTTP 200 and reached the local receiver as a structured JSON `monitoring_alert` with status `firing`. No external email, chat or pager notification was sent. The Grafana dashboard and provisioning files are included; SLO attainment over 30 days has not been measured.

The English administrator page rendered the updated catalog and tenant identity. Full inspection is selected by default for batch work. A live browser run explicitly selected rules mode for two synthetic products, displayed durable progress, completed with `2 succeeded, 0 failed`, and exposed `Continue to publication review`; no publication was executed. The authenticated CLI verification completed high/medium/pass cases with persisted report, trace, task and export round trips.

For release acceptance, a separate Compose project started from empty MySQL/Redis/Elasticsearch volumes with no provider key or private tenant registry. Its HTTP workflow passed login, unauthenticated/CSRF rejection, two-item asynchronous worker execution, report/trace reads, publication preview, allowed publication of a synthetic passing item, rejection of a high-risk item, and invalidation after editing. Full mode without a key returned a degraded report and could not authorize publication. Publication in this test affected only its disposable local test database; the main catalog and real marketplaces were untouched. Browser checks confirmed Chinese-to-English switching, product details, inspection history and disabled publication for high risk. The updated images also passed Compose health checks.

## Repeat safely

See [platform operations](platform-operations.md) for secrets, tenant creation, migration and load commands, and [SLO definitions](slo.md) for counters and limitations. Use new batch IDs for changed generation plans. Run integration tests against a separate development schema. Do not infer production authorization or legal compliance from these results.
