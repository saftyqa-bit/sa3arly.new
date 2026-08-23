from __future__ import annotations

from pathlib import Path

from pglast import parse_sql

MIGRATIONS = Path("db/migrations")
BASELINE = MIGRATIONS / "001_sa3arly_core_v2.sql"


def schema_sql() -> str:
    return BASELINE.read_text(encoding="utf-8")


def test_core_v2_is_the_only_fresh_baseline_and_parses() -> None:
    assert [path.name for path in MIGRATIONS.glob("*.sql")] == [
        "001_sa3arly_core_v2.sql"
    ]
    assert len(parse_sql(schema_sql())) >= 180


def test_core_v2_has_explicit_domain_ownership() -> None:
    sql = schema_sql()
    for schema in (
        "reference",
        "catalog",
        "merchant",
        "pricing",
        "ingestion",
        "operations",
        "governance",
        "analytics",
    ):
        assert f"CREATE SCHEMA IF NOT EXISTS {schema}" in sql
    assert "CREATE TABLE governance.cutover_runs" in sql


def test_core_v2_functions_cannot_replace_legacy_public_functions() -> None:
    sql = schema_sql()
    assert "CREATE OR REPLACE FUNCTION sa3arly_" not in sql
    for schema in ("catalog", "merchant", "pricing", "governance"):
        assert f"FUNCTION {schema}.sa3arly_" in sql


def test_catalog_supports_family_product_variant_and_typed_specs() -> None:
    sql = schema_sql()
    for table in (
        "catalog.product_families",
        "catalog.products",
        "catalog.variants",
        "catalog.variant_identifiers",
        "catalog.attribute_definitions",
        "catalog.variant_attribute_values",
    ):
        assert "CREATE TABLE" in sql and table in sql
    assert "num_nonnulls(value_text, value_number, value_boolean, value_date) = 1" in sql
    assert "ck_search_alias_exactly_one_target" in sql


def test_listing_identity_owns_urls_and_rejects_untracked_sellers() -> None:
    sql = schema_sql()
    assert "CREATE TABLE merchant.listing_urls" in sql
    assert "url_hash bytea GENERATED ALWAYS" in sql
    assert "fk_listing_seller" in sql
    assert "uq_listing_primary_url" in sql
    assert "trg_sync_listing_urls" in sql


def test_prices_use_append_only_observations_and_current_projection() -> None:
    sql = schema_sql()
    assert "CREATE TABLE IF NOT EXISTS pricing.offer_observations" in sql
    assert "current_observation_id" in sql
    assert "final_cost numeric" in sql
    assert "trg_offer_observations_append_only" in sql
    assert "trg_installment_observations_append_only" in sql
    assert "CREATE OR REPLACE VIEW pricing.public_offer_table" in sql
    assert "ORDER BY" not in sql.split("CREATE OR REPLACE VIEW pricing.public_offer_table", 1)[1]


def test_ingestion_has_one_evidence_ledger_and_recoverable_deliveries() -> None:
    sql = schema_sql()
    assert "CREATE TABLE IF NOT EXISTS ingestion.identity_clusters" in sql
    assert "CREATE TABLE IF NOT EXISTS ingestion.catalog_observations" in sql
    assert "UNIQUE (origin_type, origin_id)" in sql
    assert "CREATE TABLE IF NOT EXISTS ingestion.task_deliveries" in sql
    assert "CREATE TABLE ingestion.raw_documents" in sql


def test_serving_and_coverage_views_keep_unverified_prices_out_of_ranking() -> None:
    sql = schema_sql()
    assert "CREATE OR REPLACE VIEW pricing.public_cash_offers" in sql
    assert "o.anomaly_status = 'clear' AS eligible_for_ranking" in sql
    assert "CREATE OR REPLACE VIEW merchant.coverage_ledger" in sql
    assert "catalog_only" in sql
    assert "verified_direct_urls" in sql


def test_products_are_global_but_store_and_offer_are_market_specific() -> None:
    sql = schema_sql()
    variants_ddl = sql.split("CREATE TABLE IF NOT EXISTS catalog.variants", 1)[1].split(";", 1)[0]
    stores_ddl = sql.split("CREATE TABLE IF NOT EXISTS merchant.stores", 1)[1].split(";", 1)[0]
    offers_ddl = sql.split("CREATE TABLE IF NOT EXISTS pricing.current_offers", 1)[1].split(";", 1)[0]
    assert "country_code" not in variants_ddl
    assert "country_code" in stores_ddl or "fk_stores_country" in sql
    assert "currency text" in offers_ddl
