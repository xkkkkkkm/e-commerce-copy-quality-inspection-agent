# Enterprise integration roadmap

> Version 2 update: the [current platform guide](platform-operations.md) supersedes earlier single-tenant/public-API examples in this document. Current behavior uses LLM-led full-mode review, database-per-tenant isolation, authenticated data routes, Redis Streams, concurrent workers, JSON logs and [metrics/SLOs](slo.md). Historical roadmap items for these capabilities are now implemented; live marketplace integration and production HA remain future work.

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
| 0. Engineering platform (current) | LLM-led review, isolated tenants, Redis workers, concurrency, synthetic load, metrics/alerts and SLO definitions | Local verification does not establish production reliability or model accuracy |
| 1. Controlled pilot | One sandbox marketplace and a small internal reviewer group | Signed webhooks, platform OAuth/secret manager, external IDs, outbound publication outbox, restore drill and expert policy review |
| 2. Production foundation | One or more platforms with human-approved publishing | Versioned migration ledger, HA infrastructure, RBAC/SSO, retention controls, real paging and sustained SLO measurement |
| 3. Scale and governance | Larger tenant count and independent policy owners | Canary policies, model cost accounting, prompt/version registry, regression gates, platform reconciliation, data residency and disaster recovery |

## Highest-priority engineering gaps

1. **External integration safety:** signed webhooks, secret rotation, per-platform rate limits, an outbox with delivery status, exponential retry and reconciliation. Never treat a local `published` state as proof that the marketplace accepted the update.
2. **Identity:** database-per-tenant isolation is implemented. Add multiple members and reviewer/policy-admin roles, SSO/OIDC, lifecycle management and tenant self-service as actual pilot needs emerge.
3. **Operational reliability:** build on implemented Redis transport, failure replay, shared admission, JSON logs, metrics and alerts. Add HA, distributed evaluation scheduling, external paging and long-term SLO reporting.
4. **Data governance:** classify product and reviewer data, encrypt backups, define retention/deletion, redact logs, audit exports and protect model prompts/responses. Keep API keys in a secret manager, not `.env` on hosts.
5. **Policy quality:** version and approve rules, maintain expert-reviewed gold sets per category and locale, add regression gates and shadow mode. Do not auto-publish based only on synthetic evaluation scores.
6. **Model governance:** use a model gateway with timeout/budget controls, prompt and response versioning, citation validation, fallback behavior and human review for uncertain decisions. A model key is not a compliance control.
7. **Performance:** validate realistic payload distributions and sustained traffic beyond the measured local synthetic runs. Tune implemented pools, backpressure and concurrency to provider budgets and measured queue latency. Current SLOs are targets, not attained guarantees.

## Recommended first production slice

Start with one marketplace sandbox and human approval only. Ingest one product version at a time, use the LLM-led review with deterministic safeguards, persist the source payload and platform response, and publish through an outbound outbox after a reviewer confirms the current inspection ID. Add reconciliation of remote listing state and local audit. Model output never directly authorizes remote publication; expert-reviewed evaluation is required before expanding automation.

## Release gate

Do not advertise this repository as an enterprise-ready marketplace connector until the pilot has passed security review, dependency/image scanning, migration and restore drills, concurrency/load tests, webhook replay tests, platform sandbox reconciliation, RBAC tests and an expert-reviewed policy set. The current codebase is a strong demonstrator and a reasonable foundation, not that final state.
