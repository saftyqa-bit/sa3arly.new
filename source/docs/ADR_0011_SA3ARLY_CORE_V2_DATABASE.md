# ADR-0011: Sa3arly Core V2 database

**Status:** Accepted
**Date:** 2026-08-11
**Deciders:** Sa3arly engineering

## Context

The retired schema grew through twelve additive migrations. Product identity,
merchant links and observed prices were represented in overlapping tables, while
the public API depended on unqualified objects in the PostgreSQL `public` schema.
Sa3arly needs a global catalog, complete store coverage, typed category filters,
recoverable ingestion and a cheap-to-query current price without losing history.

## Decision

Use one PostgreSQL transactional database split into domain schemas. Canonical
catalog records, merchant listings and append-only price observations own data.
Current offers, coverage ledgers and search/ranking documents are rebuildable
projections. Raw fetched documents live in object storage and are referenced by
hash and URI. The application connection has an explicit domain search path.

The retired migration chain is replaced by the single fresh baseline
`001_sa3arly_core_v2.sql`. In production, the baseline creates namespaced V2
objects beside the legacy `public` tables. A repeatable-read data-copy job copies
and validates the live records before traffic moves; no legacy production table
is dropped during the cutover, so the previous revisions remain a rollback path.

## Options considered

### Continue additive changes

Low immediate cost, but preserves ambiguous ownership and permanent compatibility
debt. Rejected.

### Database per microservice

High isolation and independent scaling, but introduces distributed consistency,
more operations and harder product/offer transactions. Rejected for the current
scale.

### Domain schemas with canonical ledgers and projections

Strong relational integrity, one transaction boundary, simple recovery and a
clear path to export long-term history to an analytical store. Accepted.

## Consequences

- Product families, products and purchasable variants are distinct.
- Category-specific specifications are typed and filterable.
- Listing URL history is explicit and verifiable.
- Price history is append-only; current price reads stay constant-time.
- Existing databases are never reset automatically.
- Bootstrap must populate normalized catalog records before variants and offers.

## Action items

1. Take an on-demand Cloud SQL backup and pause collection dispatch.
2. Create the isolated Core V2 schemas beside the legacy `public` schema.
3. Copy live records in one repeatable-read transaction and validate critical counts.
4. Switch production only after product, store, listing, price and URL checks succeed.
5. Force a complete price refresh and retain the legacy tables for rollback.
