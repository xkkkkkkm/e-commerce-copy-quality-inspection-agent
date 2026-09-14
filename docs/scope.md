# Current scope

## Implemented

- Chinese product-copy checks for food, beauty and electronics (3C).
- Deterministic rules with Elasticsearch keyword retrieval, MySQL fallback and bundled JSON fallback.
- Optional DeepSeek semantic analysis in full mode; rules mode needs no model key.
- Structured reports, evidence, rule references, warnings, execution traces and conservative copy suggestions.
- MySQL-backed catalog, immutable revisions, version-aware inspection, audit history and a single-admin console.
- Synchronous inspection plus durable MySQL jobs with leases, heartbeats, bounded retries and cancellation.
- Single and batch inspection, batch publication preview and per-item publication transactions.
- Bilingual browser UI and deterministic synthetic merchant-submission generator.

## Deliberate exclusions

This prototype does not inspect images, connect to marketplace listing APIs, process orders, provide merchant self-service, support multi-role authorization, implement vector embeddings or reranking, or generate unrestricted copy. Publication changes only this application's catalog status. Rules and synthetic labels require domain-expert review before operational use. English UI does not imply English policy coverage.

## Data and quality boundary

The repository contains synthetic examples: 30 manually authored samples, 50 fixed evaluation cases and 33 demonstration rules. The labels are project-authored and not independently validated by domain experts. Engineering tests measure software behavior; they do not establish legal compliance or production accuracy.
