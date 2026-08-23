# ADR-0010: Unified catalog observations and recoverable task deliveries

**Status:** Superseded by ADR-0011
**Date:** 2026-08-09
**Deciders:** Sa3arly engineering

## Context

Catalog discovery and external catalog imports currently write to separate tables. Both
contain observations of the same real-world products, but neither table owns a stable
cross-source identity. External imports therefore repair existing mappings but cannot
onboard a new product, while crawler candidates without a known variant remain in an
ever-growing review backlog.

Cloud Tasks delivery is also represented only by a logical task row. When a request is
rejected or times out before the worker claims it, Cloud Tasks can exhaust and delete the
delivery while PostgreSQL continues to report the logical task as queued.

## Decision

Add a source-neutral `catalog_product_entities` identity layer and a normalized
`catalog_product_observations` evidence ledger. Both crawler candidates and external
imports attach to the same entity. Strong GTIN and brand/manufacturer-SKU identities can
merge across stores. Store/product URLs remain isolated identities until stronger
evidence appears.

A directly validated, priced EGP observation may create a source-verified variant and
offer immediately. Single-source offers stay visible but review-only; evidence from two
stores using a strong identity promotes the entity to cross-store verified.

Track every Cloud Tasks delivery generation in `catalog_task_deliveries`. A recovery
scheduler re-enqueues logical tasks whose delivery disappeared before a terminal worker
result, using a new generation while preserving the logical task and run counters.

Catalog discovery hydrates newly-seen sitemap URLs before older unresolved links. This
lets a newly launched product acquire structured title/price evidence on the first full
update, while the historical backlog is drained in bounded per-store batches that stay
inside the worker deadline and store rate limits.

## Options Considered

### Keep both ingestion tables and add more matching rules

Low migration cost, but preserves duplicate state and cannot explain or merge evidence
across sources. Rejected.

### Publish every crawler URL as a canonical product

Fastest apparent coverage, but creates category pages, duplicate variants, and unsafe
cross-store comparisons. Rejected.

### Unified evidence ledger with source-verified products

Moderate implementation cost. It preserves every observation, makes confidence
explicit, permits immediate single-store visibility, and supports later safe merging.
Accepted.

## Consequences

- External imports and crawler discovery share one product identity and evidence model.
- New priced products can become visible before cross-store corroboration without
  affecting confirmed-price rankings.
- Delivery loss becomes recoverable and auditable instead of leaving ghost queued rows.
- Store-URL fallback entities can temporarily duplicate the same product across stores;
  later strong evidence or review merges them without losing observations.
