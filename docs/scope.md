# Current scope

> Version 2 update: the [current platform guide](platform-operations.md) supersedes earlier single-tenant/public-API examples in this document. Current behavior uses LLM-led full-mode review, database-per-tenant isolation, authenticated data routes, Redis Streams, concurrent workers, JSON logs and [metrics/SLOs](slo.md). Historical roadmap items for these capabilities are now implemented; live marketplace integration and production HA remain future work.

## Implemented

- Chinese product-copy checks for food, beauty and electronics (3C).
- Deterministic rules with Elasticsearch keyword retrieval, MySQL fallback and bundled JSON fallback.
- LLM-led DeepSeek semantic analysis in full mode with mandatory rule safeguards; explicit rules baselines need no model key.
- Structured reports, evidence, rule references, warnings, execution traces and conservative copy suggestions.
- Independent MySQL schemas/accounts per tenant, immutable revisions, version-aware inspection, audit history and a tenant-admin console.
- Synchronous inspection plus durable MySQL/Redis Streams jobs, concurrent workers, item leases, heartbeats, bounded retries, cancellation and failed-job replay.
- Single and batch inspection, batch publication preview and per-item publication transactions.
- Bilingual browser UI and deterministic multi-tenant synthetic generation at 10,000+ records.
- Distributed model request/concurrency limits, tenant queue capacity, JSON logs, Prometheus/Grafana, local alert delivery and explicit SLO targets.

## Deliberate exclusions

This prototype does not inspect images, connect to marketplace listing APIs, process orders, provide merchant self-service, support multi-role authorization, implement vector embeddings or reranking, or generate unrestricted copy. Publication changes only this application's catalog status. Rules and synthetic labels require domain-expert review before operational use. English UI does not imply English policy coverage.

## Data and quality boundary

The repository contains synthetic examples: 30 manually authored samples, 50 fixed evaluation cases and 33 demonstration rules. The labels are project-authored and not independently validated by domain experts. Engineering tests measure software behavior; they do not establish legal compliance or production accuracy.
