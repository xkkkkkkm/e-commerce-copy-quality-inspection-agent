# Enterprise integration roadmap

This project can evolve into a production e-commerce copy-review service, but the current repository is a local single-machine prototype. It already has useful foundations: MySQL-backed revisions and audit history, version-aware publication guards, durable inspection jobs, deterministic rules, bounded model integration, structured evidence and a bilingual console. Production adoption requires the following staged work.

## Target integration boundary

Keep marketplace-specific credentials and APIs outside the inspection engine. Add an adapter layer per platform:

```text
Marketplace webhook / scheduled pull
        -> authenticated ingestion API
        -> canonical ProductSubmission + idempotency key
        -> durable inspection queue
        -> policy/rule/model engine
        -> review decision and audit event
        -> publication adapter (outbox + retry + reconciliation)
```

The canonical product model should retain the source platform, merchant, external product ID, source version or ETag, locale, content fields, attributes and provenance. Adapters translate platform payloads into this model and translate approved decisions back into platform-specific listing updates. The core agent must never store or log platform access tokens.

## Phased delivery

| Phase | Outcome | Required work before production use |
|---|---|---|
| 0. Prototype (current) | Local demo, synthetic data, MySQL, ES, optional DeepSeek, admin review | Not suitable for public traffic or automatic marketplace updates |
| 1. Controlled pilot | One sandbox marketplace and a small internal reviewer group | OAuth/service-account secret manager, tenant/merchant identity, webhook signature checks, idempotent ingestion, outbox table, replay tools, migration system, backups and alerting |
| 2. Production foundation | One or more platforms with human-approved publishing | Versioned migrations, broker-backed workers, distributed locks, external rate limits, dead-letter queues, immutable audit export, RBAC/SSO, PII retention policy, observability and SLOs |
| 3. Scale and governance | Multiple tenants and policy owners | Policy approval workflow, canary rules, model gateway with budgets, prompt/version registry, offline regression gates, platform reconciliation, data residency controls and disaster recovery exercises |

## Highest-priority engineering gaps

1. **External integration safety:** signed webhooks, secret rotation, per-platform rate limits, an outbox with delivery status, exponential retry and reconciliation. Never treat a local `published` state as proof that the marketplace accepted the update.
2. **Identity and tenancy:** replace the single administrator with organization, merchant, reviewer and policy-admin roles; scope every query and job by tenant; add SSO/OIDC and least-privilege service accounts.
3. **Operational reliability:** move from process-local evaluation locking and in-process throttling to shared coordination; add metrics, structured logs, tracing, alerts, readiness probes and a dead-letter workflow.
4. **Data governance:** classify product and reviewer data, encrypt backups, define retention/deletion, redact logs, audit exports and protect model prompts/responses. Keep API keys in a secret manager, not `.env` on hosts.
5. **Policy quality:** version and approve rules, maintain expert-reviewed gold sets per category and locale, add regression gates and shadow mode. Do not auto-publish based only on synthetic evaluation scores.
6. **Model governance:** use a model gateway with timeout/budget controls, prompt and response versioning, citation validation, fallback behavior and human review for uncertain decisions. A model key is not a compliance control.
7. **Performance:** benchmark realistic payloads and concurrency, add connection-pool sizing, broker backpressure and load tests, then define latency/error SLOs. Current Compose limits and worker are demonstration defaults.

## Recommended first production slice

Start with one marketplace sandbox and human approval only. Ingest one product version at a time, run rules mode by default, persist the source payload and platform response, and publish through an outbox after a reviewer confirms the current inspection ID. Add reconciliation that compares remote listing state with the local audit trail. Keep full-model analysis advisory until domain experts approve a measured, keyed evaluation set.

## Release gate

Do not advertise this repository as an enterprise-ready marketplace connector until the pilot has passed security review, dependency/image scanning, migration and restore drills, concurrency/load tests, webhook replay tests, platform sandbox reconciliation, RBAC tests and an expert-reviewed policy set. The current codebase is a strong demonstrator and a reasonable foundation, not that final state.
