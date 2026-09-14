# Metrics, alerts and service objectives

These are initial engineering objectives, not measured SLA commitments. Compose is a single-host development deployment. Use at least a representative 30-day observation period, including incidents, before claiming attainment.

| Objective | Good event / denominator | Initial target |
|---|---|---|
| API availability | Authenticated routed `/api/` responses without 5xx / all routed API responses excluding 4xx | 99.9% over 30 days |
| Async inspection completion | Successful terminal outcomes within 300 seconds of original enqueue / successful or failed terminal outcomes | 99% over 30 days |
| Control API latency | Latency of catalog/job requests excluding direct model/evaluation endpoints | Observe P95 first; provisional 1 second target |

401/403/422 and capacity 429 are excluded from the availability ratio; dashboard status counters must still be reviewed to detect rejected demand. Requests rejected before route resolution appear as `unmatched`, outside this SLI. Client cancellations and manually cancelled job items are excluded from the completion ratio. Retries include time since original enqueue; explicit replay of a terminal failed item produces another terminal observation. Queue age alerts cover work which never reaches a terminal state. Model-quality accuracy is an evaluation metric, not an infrastructure SLO.

## Metrics

| Metric | Meaning |
|---|---|
| `qa_http_requests_total{method,route,status}` | HTTP counts using route templates, not product IDs or arbitrary URLs |
| `qa_http_duration_seconds{route}` | Server request duration histogram |
| `qa_inspections_total{mode,outcome}` | Report success, partial, degraded or execution failure |
| `qa_inspection_duration_seconds{mode}` | Inspection execution time |
| `qa_llm_calls_total{purpose,outcome}` | Logical model-call outcomes; request budget separately counts every HTTP retry |
| `qa_worker_active` | In-flight inspection threads |
| `qa_worker_heartbeat_timestamp_seconds` | Most recent successful tenant reconciliation |
| `qa_tenant_reconciliation_failures` | Number of failed tenant reconciliations in the latest scan |
| `qa_queue_items{status}` | MySQL queue state summed across available tenants |
| `qa_queue_oldest_age_seconds` | Oldest queued, retrying or running item |
| `qa_job_items_total{outcome,mode}` | Worker-observed terminal success/failure, including exhausted leases |
| `qa_job_end_to_end_seconds{mode,outcome}` | Enqueue-to-terminal histogram including queue time and retries |

Metrics use process counters and histograms, not a billing ledger. A crash between a database commit and metric recording can lose an observation; the durable MySQL audit is the source for business reconciliation. Queue gauges are snapshots; a failed tenant scan means the aggregate is incomplete and raises `TenantDatabaseUnavailable`. Multiple worker replicas each report the same aggregate snapshot, so dashboards use `max`, not `sum`, for queue gauges. Configure distinct scrape targets for replicated processes.

## Alert policy

`ops/prometheus/alerts.yml` contains service-down, worker-heartbeat, per-scan tenant failure, queue age, model failure rate and completion alerts. API availability fast-burn requires both 5-minute and 1-hour error rates above 1.44% (14.4 times a 0.1% budget). Completion alerts use a one-hour bad-outcome fraction above 5% for 10 minutes; no completed traffic should not create an alert. These thresholds need calibration from normal traffic.

Prometheus evaluates every 15 seconds. Alertmanager groups and forwards alerts to the local `alert-receiver`, which writes allowlisted JSON with alert name/status. This proves local delivery, not notification of an on-call team. Set recipients and escalation policy explicitly before deployment.

```bash
docker compose -f compose.yaml -f compose.monitoring.yaml exec -T prometheus promtool check rules /etc/prometheus/alerts.yml
docker compose -f compose.yaml -f compose.monitoring.yaml exec -T -w /etc/prometheus prometheus promtool test rules alerts.test.yml
docker compose -f compose.yaml -f compose.monitoring.yaml logs --tail 20 alert-receiver
```

## Operational response

- Queue age: compare incoming rate with completion rate and LLM quotas. Slow senders on 429, inspect failed/degraded reports, then consider measured worker scaling. Do not bypass publication guards.
- Worker stalled or tenant unavailable: check Redis connectivity, MySQL availability, registry delivery and schema migration. Healthy tenants should continue. After recovery, reconcile and inspect retries; do not delete queues or reset leases indiscriminately.
- Model failures: inspect safe error codes, provider status, model availability and key configuration. API downtime, rate exhaustion, timeouts and invalid evidence are different causes. Rules-only results remain explicitly labelled.
- API burn: use request ID, templated route, error type and task ID to join JSON runtime events with durable audit records. Avoid placing customer text or secrets into operational logs.

## Runtime logs

API startup, requests, inspection completion, workers and local alerts use JSON. Fields include UTC timestamp, level, logger/event, request ID, tenant ID, task/job/item identifiers, duration and safe error type. Original message strings, exception text, HTTP headers, SQL, credentials, prompts and raw model output are omitted. Third-party runtime logs retain logger/level as generic events. Product/Agent evidence traces remain access-controlled business records in the tenant database.

The stack exports logs to stdout for collection by Docker or a deployment log shipper. Central indexing, retention controls, external paging, multi-region probes and 30-day error-budget reporting are not supplied as a managed service.
