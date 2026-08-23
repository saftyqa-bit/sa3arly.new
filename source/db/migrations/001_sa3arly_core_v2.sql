-- Sa3arly Core V2 fresh baseline.
--
-- One PostgreSQL database is split into explicit domain schemas. Canonical
-- catalog, merchant listings and append-only observations are the sources of
-- truth; current offers and public views are rebuildable serving projections.
-- This file intentionally contains no legacy schema or data-conversion DML.

-- ---------------------------------------------------------------------------
-- Domain schemas
-- ---------------------------------------------------------------------------

CREATE SCHEMA IF NOT EXISTS reference;

CREATE SCHEMA IF NOT EXISTS catalog;

CREATE SCHEMA IF NOT EXISTS merchant;

CREATE SCHEMA IF NOT EXISTS pricing;

CREATE SCHEMA IF NOT EXISTS ingestion;

CREATE SCHEMA IF NOT EXISTS operations;

CREATE SCHEMA IF NOT EXISTS governance;

CREATE SCHEMA IF NOT EXISTS analytics;

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS catalog.variants (variant_id text PRIMARY KEY, model_id text, canonical_name text NOT NULL, section text, product_type text, brand text, model text, variant_name text, ram_gb numeric(10, 2), storage_gb numeric(12, 2), color text, manufacturer_sku text, gtin text, manufacturer_url text, source_status text NOT NULL DEFAULT 'mapped', specs jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS catalog.variant_aliases (alias_variant_id text PRIMARY KEY, canonical_variant_id text NOT NULL REFERENCES catalog.variants (variant_id) ON DELETE CASCADE, reason text, created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS merchant.stores (store_id text PRIMARY KEY, name text NOT NULL, base_url text, primary_category text, coverage_categories text, store_type text, public_price_status text, online_purchase text, priority text, verification_confidence text, registry_status text, integration_difficulty text, current_mapping_count integer NOT NULL DEFAULT 0, ready_mapping_count integer NOT NULL DEFAULT 0, file_link_status text, active boolean NOT NULL DEFAULT TRUE, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS merchant.connector_configs (store_id text PRIMARY KEY REFERENCES merchant.stores (store_id) ON DELETE CASCADE, mode text NOT NULL DEFAULT 'auto', allowed_hosts text[] NOT NULL DEFAULT '{}', requests_per_minute integer NOT NULL DEFAULT 10 CHECK (requests_per_minute > 0), max_concurrency integer NOT NULL DEFAULT 2 CHECK (max_concurrency > 0), browser_required boolean NOT NULL DEFAULT FALSE, respect_robots boolean NOT NULL DEFAULT TRUE, enabled boolean NOT NULL DEFAULT TRUE, version text NOT NULL DEFAULT 'generic-v1', config jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), last_tested_at timestamptz, last_success_at timestamptz, consecutive_failures integer NOT NULL DEFAULT 0, updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS merchant.store_rate_limits (store_id text PRIMARY KEY REFERENCES merchant.stores (store_id) ON DELETE CASCADE, next_allowed_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS merchant.sellers (seller_id text PRIMARY KEY, store_id text NOT NULL REFERENCES merchant.stores (store_id) ON DELETE CASCADE, name text NOT NULL, seller_url text, verified boolean, rating numeric(4, 2), active boolean NOT NULL DEFAULT TRUE, metadata jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS merchant.listings (mapping_id text PRIMARY KEY, offer_id text, offer_key text NOT NULL, variant_id text NOT NULL REFERENCES catalog.variants (variant_id) ON DELETE CASCADE, store_id text NOT NULL REFERENCES merchant.stores (store_id) ON DELETE CASCADE, seller_id text, seller_name text, store_sku text, source_url text NOT NULL, normalized_url text NOT NULL, url_type text, direct_product_url text, title_as_seen text, match_method text, match_confidence text, evidence_level text, extraction_hint text, evidence_urls text, evidence_count integer NOT NULL DEFAULT 0, evidence_verified_at timestamptz, last_discovered_at timestamptz, last_enqueued_run_id uuid, active boolean NOT NULL DEFAULT TRUE, review_status text, metadata jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS pricing.current_offers (offer_id text PRIMARY KEY, offer_key text NOT NULL UNIQUE, mapping_id text NOT NULL UNIQUE REFERENCES merchant.listings (mapping_id) ON DELETE CASCADE, variant_id text NOT NULL REFERENCES catalog.variants (variant_id) ON DELETE CASCADE, store_id text NOT NULL REFERENCES merchant.stores (store_id) ON DELETE CASCADE, seller_id text, seller_name text, currency text NOT NULL DEFAULT 'EGP', cash_price numeric(18, 2) CHECK (cash_price IS NULL OR cash_price > 0), old_price numeric(18, 2) CHECK (old_price IS NULL OR old_price > 0), discount_amount numeric(18, 2), discount_percent numeric(9, 4), shipping_cost numeric(18, 2) CHECK (shipping_cost IS NULL OR shipping_cost >= 0), total_price numeric(18, 2) CHECK (total_price IS NULL OR total_price > 0), free_shipping boolean, availability text, available_quantity numeric(18, 2), purchase_limit numeric(18, 2), delivery_region text, delivery_text text, min_delivery_days numeric(10, 2), max_delivery_days numeric(10, 2), warranty_type text, warranty_provider text, warranty_months numeric(10, 2), store_verified boolean, seller_verified boolean, source_method text, source_url text, last_checked_at timestamptz, last_success_at timestamptz, freshness_status text NOT NULL DEFAULT 'unseen', extraction_status text NOT NULL DEFAULT 'pending', consecutive_failures integer NOT NULL DEFAULT 0, connector_version text, last_run_id uuid, active boolean NOT NULL DEFAULT TRUE, review_status text, review_notes text, raw_payload jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS pricing.offer_observations (history_id bigserial PRIMARY KEY, offer_key text NOT NULL, variant_id text NOT NULL, store_id text NOT NULL, seller_id text, observed_at timestamptz NOT NULL DEFAULT now(), run_id uuid, change_type text NOT NULL, cash_price numeric(18, 2), old_price numeric(18, 2), shipping_cost numeric(18, 2), total_price numeric(18, 2), availability text, warranty_type text, warranty_provider text, warranty_months numeric(10, 2), snapshot jsonb NOT NULL DEFAULT CAST('{}' AS jsonb));

CREATE TABLE IF NOT EXISTS ingestion.installment_tasks (task_id text PRIMARY KEY, cash_offer_key text NOT NULL, mapping_id text REFERENCES merchant.listings (mapping_id) ON DELETE CASCADE, variant_id text NOT NULL REFERENCES catalog.variants (variant_id) ON DELETE CASCADE, store_id text NOT NULL REFERENCES merchant.stores (store_id) ON DELETE CASCADE, seller_id text, source_url text NOT NULL, url_type text, status text NOT NULL DEFAULT 'pending', review_status text, title_as_seen text, notes text, active boolean NOT NULL DEFAULT TRUE, evidence_verified_at timestamptz, last_checked_at timestamptz, last_success_at timestamptz, consecutive_failures integer NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS pricing.current_installment_offers (plan_id text PRIMARY KEY, plan_key text NOT NULL UNIQUE, cash_offer_key text NOT NULL, variant_id text NOT NULL REFERENCES catalog.variants (variant_id) ON DELETE CASCADE, store_id text NOT NULL REFERENCES merchant.stores (store_id) ON DELETE CASCADE, seller_id text, seller_name text, provider_id text, provider_name text, provider_type text, bank_or_card text, plan_name text, months integer CHECK (months IS NULL OR months > 0), payment_frequency text NOT NULL DEFAULT 'monthly', periodic_payment numeric(18, 2) CHECK (periodic_payment IS NULL OR periodic_payment > 0), first_payment numeric(18, 2) CHECK (first_payment IS NULL OR first_payment >= 0), down_payment numeric(18, 2) CHECK (down_payment IS NULL OR down_payment >= 0), down_payment_percent numeric(9, 4), admin_fees numeric(18, 2) CHECK (admin_fees IS NULL OR admin_fees >= 0), processing_fees numeric(18, 2) CHECK (processing_fees IS NULL OR processing_fees >= 0), insurance_fees numeric(18, 2) CHECK (insurance_fees IS NULL OR insurance_fees >= 0), other_fees numeric(18, 2) CHECK (other_fees IS NULL OR other_fees >= 0), total_published numeric(18, 2) CHECK (total_published IS NULL OR total_published > 0), total_calculated numeric(18, 2) CHECK (total_calculated IS NULL OR total_calculated > 0), cash_price_at_observation numeric(18, 2), financing_cost numeric(18, 2), financing_markup_percent numeric(9, 4), apr numeric(9, 4), interest_type text, interest_free boolean, grace_months integer, minimum_purchase numeric(18, 2), maximum_financing numeric(18, 2), eligibility text, required_card text, customer_type text, new_customers_only boolean, geography text, starts_at timestamptz, ends_at timestamptz, promo_code text, terms_url text, source_url text, starting_from_only boolean NOT NULL DEFAULT FALSE, completeness text, last_checked_at timestamptz, last_success_at timestamptz, freshness_status text NOT NULL DEFAULT 'unseen', extraction_status text NOT NULL DEFAULT 'pending', consecutive_failures integer NOT NULL DEFAULT 0, connector_version text, last_run_id uuid, active boolean NOT NULL DEFAULT TRUE, review_status text, review_notes text, raw_payload jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS pricing.installment_observations (history_id bigserial PRIMARY KEY, plan_key text NOT NULL, cash_offer_key text NOT NULL, variant_id text NOT NULL, store_id text NOT NULL, observed_at timestamptz NOT NULL DEFAULT now(), run_id uuid, change_type text NOT NULL, snapshot jsonb NOT NULL DEFAULT CAST('{}' AS jsonb));

CREATE TABLE IF NOT EXISTS operations.price_runs (run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), run_slot timestamptz NOT NULL UNIQUE, trigger_source text NOT NULL, status text NOT NULL DEFAULT 'created', started_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz, mapping_count integer NOT NULL DEFAULT 0, url_group_count integer NOT NULL DEFAULT 0, queued_task_count integer NOT NULL DEFAULT 0, completed_task_count integer NOT NULL DEFAULT 0, successful_task_count integer NOT NULL DEFAULT 0, failed_task_count integer NOT NULL DEFAULT 0, cash_updates integer NOT NULL DEFAULT 0, installment_updates integer NOT NULL DEFAULT 0, discovered_urls integer NOT NULL DEFAULT 0, metadata jsonb NOT NULL DEFAULT CAST('{}' AS jsonb));

CREATE TABLE IF NOT EXISTS operations.price_tasks (task_run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), external_task_id text UNIQUE, run_id uuid NOT NULL REFERENCES operations.price_runs (run_id) ON DELETE CASCADE, store_id text NOT NULL, source_url text NOT NULL, url_type text, mapping_count integer NOT NULL, scheduled_for timestamptz, status text NOT NULL DEFAULT 'queued', attempt integer NOT NULL DEFAULT 1, started_at timestamptz, completed_at timestamptz, http_status integer, response_bytes integer, cash_updates integer NOT NULL DEFAULT 0, installment_updates integer NOT NULL DEFAULT 0, discovered_urls integer NOT NULL DEFAULT 0, error_code text, error_message text, metrics jsonb NOT NULL DEFAULT CAST('{}' AS jsonb));

CREATE TABLE IF NOT EXISTS ingestion.page_cache (store_id text NOT NULL, source_url text NOT NULL, etag text, last_modified text, content_hash text, http_status integer, content_type text, parsed_payload jsonb, fetched_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (store_id, source_url));

CREATE TABLE IF NOT EXISTS governance.system_alerts (alert_id bigserial PRIMARY KEY, severity text NOT NULL, alert_type text NOT NULL, store_id text, mapping_id text, offer_key text, message text NOT NULL, context jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), created_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz);

CREATE TABLE IF NOT EXISTS ingestion.discovery_runs (run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), run_slot timestamptz NOT NULL UNIQUE, trigger_source text NOT NULL, status text NOT NULL DEFAULT 'created', source_count integer NOT NULL DEFAULT 0, queued_task_count integer NOT NULL DEFAULT 0, completed_task_count integer NOT NULL DEFAULT 0, successful_task_count integer NOT NULL DEFAULT 0, failed_task_count integer NOT NULL DEFAULT 0, candidates_seen integer NOT NULL DEFAULT 0, candidates_new integer NOT NULL DEFAULT 0, mappings_created integer NOT NULL DEFAULT 0, provisional_products integer NOT NULL DEFAULT 0, verified_products integer NOT NULL DEFAULT 0, metadata jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, completed_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS ingestion.discovery_sources (source_id text PRIMARY KEY, store_id text NOT NULL REFERENCES merchant.stores (store_id) ON DELETE CASCADE, source_url text NOT NULL, normalized_url text NOT NULL, source_type text NOT NULL DEFAULT 'auto', enabled boolean NOT NULL DEFAULT TRUE, status text NOT NULL DEFAULT 'pending', priority text, consecutive_failures integer NOT NULL DEFAULT 0, last_scan_at timestamptz, last_success_at timestamptz, next_scan_at timestamptz NOT NULL DEFAULT now(), etag text, last_modified text, last_error_code text, last_error_message text, metadata jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS ingestion.discovery_tasks (task_id text PRIMARY KEY, run_id uuid NOT NULL REFERENCES ingestion.discovery_runs (run_id) ON DELETE CASCADE, source_id text NOT NULL REFERENCES ingestion.discovery_sources (source_id) ON DELETE CASCADE, store_id text NOT NULL REFERENCES merchant.stores (store_id) ON DELETE CASCADE, source_url text NOT NULL, status text NOT NULL DEFAULT 'queued', scheduled_for timestamptz NOT NULL, attempt_count integer NOT NULL DEFAULT 0, http_status integer, response_bytes bigint NOT NULL DEFAULT 0, candidates_seen integer NOT NULL DEFAULT 0, candidates_new integer NOT NULL DEFAULT 0, mappings_created integer NOT NULL DEFAULT 0, provisional_products integer NOT NULL DEFAULT 0, verified_products integer NOT NULL DEFAULT 0, error_code text, error_message text, metrics jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, completed_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS ingestion.discovery_candidates (candidate_id text PRIMARY KEY, store_id text NOT NULL REFERENCES merchant.stores (store_id) ON DELETE CASCADE, source_id text NOT NULL REFERENCES ingestion.discovery_sources (source_id) ON DELETE CASCADE, normalized_url text NOT NULL, source_url text NOT NULL, title text NOT NULL, brand text, sku text, gtin text, fingerprint text, currency text, observed_price numeric(18, 2), availability text, source_method text, status text NOT NULL DEFAULT 'pending_match', proposed_variant_id text REFERENCES catalog.variants (variant_id) ON DELETE SET NULL, mapping_id text REFERENCES merchant.listings (mapping_id) ON DELETE SET NULL, match_score numeric(10, 3), match_method text, evidence_store_count integer NOT NULL DEFAULT 1, first_seen_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(), last_run_id uuid REFERENCES ingestion.discovery_runs (run_id) ON DELETE SET NULL, raw_payload jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), review_status text, review_notes text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS reference.countries (country_code char(2) PRIMARY KEY, name_ar text NOT NULL, name_en text NOT NULL, default_currency char(3) NOT NULL, active boolean NOT NULL DEFAULT TRUE, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS reference.currencies (currency_code char(3) PRIMARY KEY, name_ar text NOT NULL, name_en text NOT NULL, minor_unit smallint NOT NULL DEFAULT 2 CHECK (minor_unit BETWEEN 0 AND 4), active boolean NOT NULL DEFAULT TRUE, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS catalog.categories (category_id text PRIMARY KEY, parent_category_id text REFERENCES catalog.categories (category_id) ON DELETE SET NULL, source_key text NOT NULL UNIQUE, slug text NOT NULL UNIQUE, name_ar text NOT NULL, name_en text, level smallint NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 8), active boolean NOT NULL DEFAULT TRUE, sort_order integer NOT NULL DEFAULT 0, metadata jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS catalog.brands (brand_id text PRIMARY KEY, slug text NOT NULL UNIQUE, name text NOT NULL, normalized_name text NOT NULL UNIQUE, active boolean NOT NULL DEFAULT TRUE, metadata jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS catalog.products (product_id text PRIMARY KEY, category_id text REFERENCES catalog.categories (category_id) ON DELETE SET NULL, brand_id text REFERENCES catalog.brands (brand_id) ON DELETE SET NULL, canonical_name text NOT NULL, model text, source_status text NOT NULL DEFAULT 'mapped', specs jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), active boolean NOT NULL DEFAULT TRUE, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE (category_id, brand_id, model));

CREATE TABLE IF NOT EXISTS governance.review_cases (review_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), entity_type text NOT NULL, entity_id text NOT NULL, issue_code text NOT NULL, severity text NOT NULL DEFAULT 'medium' CHECK (severity IN ('low', 'medium', 'high', 'critical')), status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'in_review', 'resolved', 'rejected', 'ignored')), title text NOT NULL, description text, payload jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), assigned_to text, resolution text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz);

CREATE TABLE IF NOT EXISTS governance.audit_events (audit_id bigserial PRIMARY KEY, entity_type text NOT NULL, entity_id text, action text NOT NULL, actor text, request_id text, before_data jsonb, after_data jsonb, created_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS catalog.search_aliases (alias_id bigserial PRIMARY KEY, variant_id text REFERENCES catalog.variants (variant_id) ON DELETE CASCADE, product_id text REFERENCES catalog.products (product_id) ON DELETE CASCADE, alias_text text NOT NULL, normalized_alias text NOT NULL, source text NOT NULL DEFAULT 'manual', confidence numeric(6, 3) NOT NULL DEFAULT 100 CHECK (confidence BETWEEN 0 AND 100), active boolean NOT NULL DEFAULT TRUE, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), CHECK (variant_id IS NOT NULL OR product_id IS NOT NULL));

CREATE TABLE IF NOT EXISTS merchant.store_quality_metrics (store_id text PRIMARY KEY REFERENCES merchant.stores (store_id) ON DELETE CASCADE, price_accuracy_score numeric(6, 3) CHECK (price_accuracy_score IS NULL OR price_accuracy_score BETWEEN 0 AND 100), update_regularity_score numeric(6, 3) CHECK (update_regularity_score IS NULL OR update_regularity_score BETWEEN 0 AND 100), availability_clarity_score numeric(6, 3) CHECK (availability_clarity_score IS NULL OR availability_clarity_score BETWEEN 0 AND 100), warranty_clarity_score numeric(6, 3) CHECK (warranty_clarity_score IS NULL OR warranty_clarity_score BETWEEN 0 AND 100), correct_destination_score numeric(6, 3) CHECK (correct_destination_score IS NULL OR correct_destination_score BETWEEN 0 AND 100), broken_link_rate numeric(7, 4) CHECK (broken_link_rate IS NULL OR broken_link_rate BETWEEN 0 AND 1), complaint_response_score numeric(6, 3) CHECK (complaint_response_score IS NULL OR complaint_response_score BETWEEN 0 AND 100), sample_size integer NOT NULL DEFAULT 0 CHECK (sample_size >= 0), calculation_window_days integer NOT NULL DEFAULT 90 CHECK (calculation_window_days > 0), evidence jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), calculated_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS pricing.alert_rules (alert_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), variant_id text NOT NULL REFERENCES catalog.variants (variant_id) ON DELETE CASCADE, store_id text REFERENCES merchant.stores (store_id) ON DELETE CASCADE, rule_type text NOT NULL CHECK (rule_type IN ('below_amount', 'at_90_day_low', 'interest_free_installment', 'store_available', 'back_in_stock', 'coupon_available', 'final_cost_drop', 'weekly_wishlist_digest')), threshold_amount numeric(18, 2), currency text NOT NULL DEFAULT 'EGP', channel text NOT NULL CHECK (channel IN ('local', 'email', 'browser', 'whatsapp')), delivery_status text NOT NULL DEFAULT 'awaiting_provider' CHECK (delivery_status IN ('local_only', 'awaiting_provider', 'pending_verification', 'active', 'paused', 'disabled')), channel_config jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), consent_at timestamptz, last_triggered_at timestamptz, active boolean NOT NULL DEFAULT TRUE, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS pricing.price_reports (report_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), offer_id text, plan_id text, variant_id text NOT NULL REFERENCES catalog.variants (variant_id) ON DELETE CASCADE, store_id text REFERENCES merchant.stores (store_id) ON DELETE SET NULL, report_type text NOT NULL CHECK (report_type IN ('wrong_price', 'wrong_variant', 'wrong_availability', 'broken_link', 'shipping_mismatch', 'coupon_invalid', 'warranty_mismatch', 'other')), description text, reporter_fingerprint text, evidence_url text, status text NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'triaged', 'confirmed', 'rejected', 'resolved')), resolution_notes text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), resolved_at timestamptz, CHECK (offer_id IS NOT NULL OR plan_id IS NOT NULL));

CREATE TABLE IF NOT EXISTS pricing.comparison_shares (share_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), variant_ids text[] NOT NULL, settings jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), snapshot jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), expires_at timestamptz NOT NULL DEFAULT now() + CAST('30 days' AS interval), created_at timestamptz NOT NULL DEFAULT now(), CHECK (cardinality(variant_ids) BETWEEN 2 AND 4));

CREATE TABLE IF NOT EXISTS ingestion.import_runs (import_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), provider text NOT NULL, external_run_id text NOT NULL, store_id text NOT NULL REFERENCES merchant.stores (store_id) ON DELETE CASCADE, status text NOT NULL DEFAULT 'processing' CHECK (status IN ('processing', 'completed', 'completed_with_review', 'failed', 'dry_run')), rows_received integer NOT NULL DEFAULT 0, rows_accepted integer NOT NULL DEFAULT 0, rows_rejected integer NOT NULL DEFAULT 0, rows_matched integer NOT NULL DEFAULT 0, mappings_created integer NOT NULL DEFAULT 0, mappings_repaired integer NOT NULL DEFAULT 0, prices_observed integer NOT NULL DEFAULT 0, review_count integer NOT NULL DEFAULT 0, metadata jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), error_message text, created_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz, updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE (provider, external_run_id, store_id));

CREATE TABLE IF NOT EXISTS ingestion.import_items (item_id text PRIMARY KEY, import_id uuid NOT NULL REFERENCES ingestion.import_runs (import_id) ON DELETE CASCADE, store_id text NOT NULL REFERENCES merchant.stores (store_id) ON DELETE CASCADE, source_url text, normalized_url text, title text, brand text, merchant_sku text, manufacturer_sku text, gtin text, observed_price numeric(18, 2), currency text, availability text, image_url text, validation_status text NOT NULL, rejection_code text, proposed_variant_id text REFERENCES catalog.variants (variant_id) ON DELETE SET NULL, mapping_id text REFERENCES merchant.listings (mapping_id) ON DELETE SET NULL, match_method text, match_score numeric(10, 3), data_hash char(64) NOT NULL, evidence jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), raw_payload jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), first_seen_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE (import_id, data_hash));

CREATE TABLE IF NOT EXISTS ingestion.identity_clusters (entity_id text PRIMARY KEY, identity_key text NOT NULL UNIQUE, identity_strength smallint NOT NULL CHECK (identity_strength BETWEEN 0 AND 100), canonical_title text NOT NULL, normalized_title text NOT NULL, brand text, normalized_brand text, manufacturer_sku text, gtin text, image_url text, status text NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'source_verified', 'cross_store_verified', 'merged', 'rejected')), evidence_store_count integer NOT NULL DEFAULT 0, evidence_count integer NOT NULL DEFAULT 0, priced_evidence_count integer NOT NULL DEFAULT 0, promoted_variant_id text REFERENCES catalog.variants (variant_id) ON DELETE SET NULL, merged_into_entity_id text REFERENCES ingestion.identity_clusters (entity_id) ON DELETE SET NULL, metadata jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), first_seen_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS ingestion.catalog_observations (observation_id text PRIMARY KEY, entity_id text NOT NULL REFERENCES ingestion.identity_clusters (entity_id) ON DELETE CASCADE, origin_type text NOT NULL CHECK (origin_type IN ('catalog_discovery', 'catalog_import')), origin_id text NOT NULL, store_id text NOT NULL REFERENCES merchant.stores (store_id) ON DELETE CASCADE, normalized_url text NOT NULL, source_url text, title text NOT NULL, normalized_title text NOT NULL, brand text, manufacturer_sku text, merchant_sku text, gtin text, observed_price numeric(18, 2), currency text, availability text, image_url text, validation_status text NOT NULL DEFAULT 'accepted', publishable boolean NOT NULL DEFAULT FALSE, confidence_score smallint NOT NULL DEFAULT 0 CHECK (confidence_score BETWEEN 0 AND 100), raw_payload jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), first_seen_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE (origin_type, origin_id));

CREATE TABLE IF NOT EXISTS ingestion.task_deliveries (delivery_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), task_id text NOT NULL REFERENCES ingestion.discovery_tasks (task_id) ON DELETE CASCADE, generation integer NOT NULL, queue_task_name text, status text NOT NULL DEFAULT 'prepared' CHECK (status IN ('prepared', 'enqueued', 'dispatched', 'succeeded', 'failed', 'lost', 'superseded')), dispatch_count integer NOT NULL DEFAULT 0, response_code integer, error_code text, error_message text, enqueued_at timestamptz, dispatched_at timestamptz, completed_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE (task_id, generation));

CREATE TABLE catalog.product_families (family_id text PRIMARY KEY, category_id text REFERENCES catalog.categories (category_id) ON DELETE SET NULL, brand_id text REFERENCES catalog.brands (brand_id) ON DELETE SET NULL, canonical_name text NOT NULL, normalized_name text NOT NULL, generation text, active boolean NOT NULL DEFAULT TRUE, metadata jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE (category_id, brand_id, normalized_name));

CREATE TABLE catalog.variant_identifiers (identifier_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), variant_id text NOT NULL REFERENCES catalog.variants (variant_id) ON DELETE CASCADE, identifier_type text NOT NULL CHECK (identifier_type IN ('gtin', 'ean', 'upc', 'isbn', 'mpn', 'manufacturer_sku')), identifier_value text NOT NULL, normalized_value text NOT NULL, authority text NOT NULL DEFAULT '', is_primary boolean NOT NULL DEFAULT FALSE, created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (variant_id, identifier_type, normalized_value), UNIQUE (identifier_type, normalized_value, authority));

CREATE TABLE catalog.attribute_definitions (attribute_id text PRIMARY KEY, category_id text NOT NULL REFERENCES catalog.categories (category_id) ON DELETE CASCADE, code text NOT NULL, name_ar text NOT NULL, name_en text, value_type text NOT NULL CHECK (value_type IN ('text', 'number', 'boolean', 'date')), default_unit text, filterable boolean NOT NULL DEFAULT TRUE, comparable boolean NOT NULL DEFAULT TRUE, required_for_identity boolean NOT NULL DEFAULT FALSE, sort_order integer NOT NULL DEFAULT 0, validation jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(), UNIQUE (category_id, code));

CREATE TABLE catalog.variant_attribute_values (variant_id text NOT NULL REFERENCES catalog.variants (variant_id) ON DELETE CASCADE, attribute_id text NOT NULL REFERENCES catalog.attribute_definitions (attribute_id) ON DELETE CASCADE, value_index smallint NOT NULL DEFAULT 0 CHECK (value_index >= 0), value_text text, value_number numeric(24, 6), value_boolean boolean, value_date date, unit text, normalized_text text, source text NOT NULL DEFAULT 'catalog', confidence numeric(6, 3) NOT NULL DEFAULT 100 CHECK (confidence BETWEEN 0 AND 100), updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (variant_id, attribute_id, value_index), CHECK (num_nonnulls(value_text, value_number, value_boolean, value_date) = 1));

CREATE TABLE merchant.listing_urls (listing_url_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), listing_id text NOT NULL REFERENCES merchant.listings (mapping_id) ON DELETE CASCADE, url text NOT NULL, normalized_url text NOT NULL, url_hash bytea GENERATED ALWAYS AS (digest(normalized_url, 'sha256')) STORED, url_kind text NOT NULL DEFAULT 'product' CHECK (url_kind IN ('product', 'canonical', 'redirect', 'source', 'archive')), status text NOT NULL DEFAULT 'unverified' CHECK (status IN ('unverified', 'verified', 'redirected', 'broken', 'blocked')), is_primary boolean NOT NULL DEFAULT FALSE, http_status integer, redirect_target text, first_seen_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz NOT NULL DEFAULT now(), last_verified_at timestamptz, metadata jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), UNIQUE (listing_id, url_hash));

CREATE TABLE ingestion.raw_documents (document_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), store_id text REFERENCES merchant.stores (store_id) ON DELETE SET NULL, source_url text NOT NULL, content_hash char(64) NOT NULL, object_uri text NOT NULL, content_type text, byte_size bigint CHECK (byte_size IS NULL OR byte_size >= 0), fetched_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz, metadata jsonb NOT NULL DEFAULT CAST('{}' AS jsonb), UNIQUE (content_hash, object_uri));

CREATE TABLE governance.outbox_events (event_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), aggregate_type text NOT NULL, aggregate_id text NOT NULL, event_type text NOT NULL, payload jsonb NOT NULL, occurred_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz, attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0), last_error text);

CREATE TABLE governance.cutover_runs (cutover_id uuid PRIMARY KEY DEFAULT gen_random_uuid(), source_schema text NOT NULL DEFAULT 'public', status text NOT NULL CHECK (status IN ('completed', 'verified')), source_counts jsonb NOT NULL, target_counts jsonb NOT NULL, started_at timestamptz NOT NULL, completed_at timestamptz NOT NULL DEFAULT now());

CREATE TABLE analytics.popularity_scores (subject_type text NOT NULL CHECK (subject_type IN ('category', 'brand', 'family', 'product', 'variant')), subject_id text NOT NULL, country_code char(2) NOT NULL REFERENCES reference.countries (country_code), window_days smallint NOT NULL CHECK (window_days IN (1, 7, 30, 90, 365)), score numeric(20, 6) NOT NULL DEFAULT 0, view_count bigint NOT NULL DEFAULT 0 CHECK (view_count >= 0), selection_count bigint NOT NULL DEFAULT 0 CHECK (selection_count >= 0), comparison_count bigint NOT NULL DEFAULT 0 CHECK (comparison_count >= 0), calculated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (subject_type, subject_id, country_code, window_days));

-- ---------------------------------------------------------------------------
-- Constraints and final columns
-- ---------------------------------------------------------------------------

ALTER TABLE catalog.variants ADD COLUMN IF NOT EXISTS product_id text, ADD COLUMN IF NOT EXISTS category_id text, ADD COLUMN IF NOT EXISTS brand_id text, ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT TRUE;

ALTER TABLE merchant.listings ADD COLUMN IF NOT EXISTS manufacturer_sku text;

ALTER TABLE pricing.current_offers ADD COLUMN IF NOT EXISTS mandatory_fees numeric(18, 2) CHECK (mandatory_fees IS NULL OR mandatory_fees >= 0), ADD COLUMN IF NOT EXISTS card_fees numeric(18, 2) CHECK (card_fees IS NULL OR card_fees >= 0), ADD COLUMN IF NOT EXISTS coupon_code text, ADD COLUMN IF NOT EXISTS coupon_discount numeric(18, 2) CHECK (coupon_discount IS NULL OR coupon_discount >= 0), ADD COLUMN IF NOT EXISTS coupon_validated_at timestamptz, ADD COLUMN IF NOT EXISTS pickup_available boolean, ADD COLUMN IF NOT EXISTS pickup_text text, ADD COLUMN IF NOT EXISTS availability_verified_at timestamptz, ADD COLUMN IF NOT EXISTS match_quality_score numeric(6, 3) CHECK (match_quality_score IS NULL OR match_quality_score BETWEEN 0 AND 100), ADD COLUMN IF NOT EXISTS anomaly_status text NOT NULL DEFAULT 'clear' CHECK (anomaly_status IN ('clear', 'review', 'blocked')), ADD COLUMN IF NOT EXISTS anomaly_reasons jsonb NOT NULL DEFAULT CAST('[]' AS jsonb), ADD COLUMN IF NOT EXISTS decision_metadata jsonb NOT NULL DEFAULT CAST('{}' AS jsonb);

ALTER TABLE pricing.current_installment_offers ADD COLUMN IF NOT EXISTS shipping_cost numeric(18, 2) CHECK (shipping_cost IS NULL OR shipping_cost >= 0), ADD COLUMN IF NOT EXISTS card_fees numeric(18, 2) CHECK (card_fees IS NULL OR card_fees >= 0), ADD COLUMN IF NOT EXISTS coupon_discount numeric(18, 2) CHECK (coupon_discount IS NULL OR coupon_discount >= 0), ADD COLUMN IF NOT EXISTS pickup_available boolean, ADD COLUMN IF NOT EXISTS availability text, ADD COLUMN IF NOT EXISTS warranty_type text, ADD COLUMN IF NOT EXISTS warranty_provider text, ADD COLUMN IF NOT EXISTS warranty_months numeric(10, 2), ADD COLUMN IF NOT EXISTS match_quality_score numeric(6, 3) CHECK (match_quality_score IS NULL OR match_quality_score BETWEEN 0 AND 100), ADD COLUMN IF NOT EXISTS anomaly_status text NOT NULL DEFAULT 'clear' CHECK (anomaly_status IN ('clear', 'review', 'blocked')), ADD COLUMN IF NOT EXISTS anomaly_reasons jsonb NOT NULL DEFAULT CAST('[]' AS jsonb), ADD COLUMN IF NOT EXISTS decision_metadata jsonb NOT NULL DEFAULT CAST('{}' AS jsonb);

ALTER TABLE catalog.variants ADD COLUMN IF NOT EXISTS search_document text NOT NULL DEFAULT '', ADD COLUMN IF NOT EXISTS search_aliases text[] NOT NULL DEFAULT '{}';

ALTER TABLE catalog.variants ADD COLUMN IF NOT EXISTS image_url text;

ALTER TABLE merchant.listings ADD COLUMN IF NOT EXISTS direct_url_status text NOT NULL DEFAULT 'missing', ADD COLUMN IF NOT EXISTS direct_url_verified_at timestamptz, ADD COLUMN IF NOT EXISTS direct_url_source text, ADD COLUMN IF NOT EXISTS direct_url_evidence jsonb NOT NULL DEFAULT CAST('{}' AS jsonb);

ALTER TABLE ingestion.discovery_candidates ADD COLUMN IF NOT EXISTS entity_id text REFERENCES ingestion.identity_clusters (entity_id) ON DELETE SET NULL;

ALTER TABLE ingestion.import_items ADD COLUMN IF NOT EXISTS entity_id text REFERENCES ingestion.identity_clusters (entity_id) ON DELETE SET NULL;

ALTER TABLE ingestion.discovery_tasks ADD COLUMN IF NOT EXISTS delivery_generation integer NOT NULL DEFAULT 1, ADD COLUMN IF NOT EXISTS queue_task_name text, ADD COLUMN IF NOT EXISTS last_enqueued_at timestamptz, ADD COLUMN IF NOT EXISTS last_heartbeat_at timestamptz, ADD COLUMN IF NOT EXISTS recovery_count integer NOT NULL DEFAULT 0, ADD COLUMN IF NOT EXISTS recovery_after timestamptz;

ALTER TABLE ingestion.discovery_candidates ADD COLUMN IF NOT EXISTS reconcile_version integer NOT NULL DEFAULT 0, ADD COLUMN IF NOT EXISTS reconcile_checked_at timestamptz;

ALTER TABLE catalog.variants ADD CONSTRAINT fk_variants_product FOREIGN KEY (product_id) REFERENCES catalog.products (product_id) ON DELETE SET NULL, ADD CONSTRAINT fk_variants_category FOREIGN KEY (category_id) REFERENCES catalog.categories (category_id) ON DELETE SET NULL, ADD CONSTRAINT fk_variants_brand FOREIGN KEY (brand_id) REFERENCES catalog.brands (brand_id) ON DELETE SET NULL;

ALTER TABLE reference.countries ADD CONSTRAINT fk_countries_default_currency FOREIGN KEY (default_currency) REFERENCES reference.currencies (currency_code);

ALTER TABLE merchant.stores ADD COLUMN IF NOT EXISTS country_code char(2) NOT NULL DEFAULT 'EG', ADD CONSTRAINT fk_stores_country FOREIGN KEY (country_code) REFERENCES reference.countries (country_code);

ALTER TABLE catalog.products ADD COLUMN IF NOT EXISTS family_id text REFERENCES catalog.product_families (family_id) ON DELETE SET NULL;

ALTER TABLE catalog.search_aliases ADD CONSTRAINT ck_search_alias_exactly_one_target CHECK ((variant_id IS NULL) <> (product_id IS NULL));

ALTER TABLE merchant.listings ADD CONSTRAINT uq_listing_identity_tuple UNIQUE (mapping_id, variant_id, store_id), ADD CONSTRAINT fk_listing_seller FOREIGN KEY (seller_id) REFERENCES merchant.sellers (seller_id) ON DELETE SET NULL, ADD CONSTRAINT ck_listing_direct_url_status CHECK (direct_url_status IN ('missing', 'legacy_unverified', 'verified', 'failed', 'conflict'));

ALTER TABLE pricing.current_offers ADD COLUMN current_observation_id bigint, ADD COLUMN final_cost numeric(18, 2) GENERATED ALWAYS AS (CASE WHEN cash_price IS NULL THEN NULL ELSE GREATEST((cash_price + COALESCE(shipping_cost, 0) + COALESCE(mandatory_fees, 0) + COALESCE(card_fees, 0)) - COALESCE(coupon_discount, 0), 0) END) STORED, ADD CONSTRAINT fk_current_offer_listing_identity FOREIGN KEY (mapping_id, variant_id, store_id) REFERENCES merchant.listings (mapping_id, variant_id, store_id), ADD CONSTRAINT fk_current_offer_seller FOREIGN KEY (seller_id) REFERENCES merchant.sellers (seller_id) ON DELETE SET NULL;

ALTER TABLE pricing.offer_observations ADD COLUMN listing_id text REFERENCES merchant.listings (mapping_id) ON DELETE CASCADE, ADD COLUMN currency char(3) REFERENCES reference.currencies (currency_code), ADD COLUMN mandatory_fees numeric(18, 2) CHECK (mandatory_fees IS NULL OR mandatory_fees >= 0), ADD COLUMN card_fees numeric(18, 2) CHECK (card_fees IS NULL OR card_fees >= 0), ADD COLUMN coupon_discount numeric(18, 2) CHECK (coupon_discount IS NULL OR coupon_discount >= 0), ADD COLUMN quality_status text NOT NULL DEFAULT 'review' CHECK (quality_status IN ('verified', 'review', 'blocked')), ADD COLUMN quality_reasons jsonb NOT NULL DEFAULT CAST('[]' AS jsonb), ADD COLUMN idempotency_key uuid NOT NULL DEFAULT gen_random_uuid(), ADD CONSTRAINT uq_offer_observation_idempotency UNIQUE (idempotency_key);

ALTER TABLE pricing.current_offers ADD CONSTRAINT fk_current_offer_observation FOREIGN KEY (current_observation_id) REFERENCES pricing.offer_observations (history_id) ON DELETE SET NULL;

ALTER TABLE pricing.current_installment_offers ADD CONSTRAINT fk_installment_current_offer FOREIGN KEY (cash_offer_key) REFERENCES pricing.current_offers (offer_key) ON DELETE CASCADE;

-- ---------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------

INSERT INTO reference.currencies (currency_code, name_ar, name_en, minor_unit) VALUES ('EGP', 'الجنيه المصري', 'Egyptian Pound', 2) ON CONFLICT (currency_code) DO UPDATE SET name_ar = excluded.name_ar, name_en = excluded.name_en, minor_unit = excluded.minor_unit, updated_at = now();

INSERT INTO reference.countries (country_code, name_ar, name_en, default_currency) VALUES ('EG', 'مصر', 'Egypt', 'EGP') ON CONFLICT (country_code) DO UPDATE SET name_ar = excluded.name_ar, name_en = excluded.name_en, default_currency = excluded.default_currency, updated_at = now();

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------

CREATE INDEX IF NOT EXISTS idx_product_variants_name_trgm ON catalog.variants USING gin (canonical_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_product_variants_brand_model ON catalog.variants (brand, model);

CREATE INDEX IF NOT EXISTS idx_product_variants_section_brand_model ON catalog.variants (section, brand, model);

CREATE INDEX IF NOT EXISTS idx_product_variants_gtin ON catalog.variants (gtin) WHERE gtin IS NOT NULL AND gtin <> '';

CREATE INDEX IF NOT EXISTS idx_product_variants_sku ON catalog.variants (manufacturer_sku) WHERE manufacturer_sku IS NOT NULL AND manufacturer_sku <> '';

CREATE INDEX IF NOT EXISTS idx_stores_active_priority ON merchant.stores (active, priority);

CREATE UNIQUE INDEX IF NOT EXISTS uq_mapping_identity ON merchant.listings (variant_id, store_id, (COALESCE(seller_id, '')), normalized_url);

CREATE INDEX IF NOT EXISTS idx_mappings_store_url ON merchant.listings (store_id, normalized_url) WHERE active;

CREATE INDEX IF NOT EXISTS idx_mappings_variant ON merchant.listings (variant_id) WHERE active;

CREATE INDEX IF NOT EXISTS idx_cash_variant_price ON pricing.current_offers (variant_id, total_price, cash_price) WHERE active;

CREATE INDEX IF NOT EXISTS idx_cash_store ON pricing.current_offers (store_id, last_success_at DESC);

CREATE INDEX IF NOT EXISTS idx_cash_freshness ON pricing.current_offers (last_success_at DESC) WHERE cash_price IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_cash_history_offer_time ON pricing.offer_observations (offer_key, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_cash_history_variant_time ON pricing.offer_observations (variant_id, observed_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_installment_task_offer ON ingestion.installment_tasks (cash_offer_key);

CREATE INDEX IF NOT EXISTS idx_installment_tasks_store ON ingestion.installment_tasks (store_id, active, status);

CREATE INDEX IF NOT EXISTS idx_installment_variant ON pricing.current_installment_offers (variant_id, months, periodic_payment) WHERE active;

CREATE INDEX IF NOT EXISTS idx_installment_offer ON pricing.current_installment_offers (cash_offer_key) WHERE active;

CREATE INDEX IF NOT EXISTS idx_installment_provider ON pricing.current_installment_offers (provider_name, bank_or_card);

CREATE INDEX IF NOT EXISTS idx_installment_history_plan_time ON pricing.installment_observations (plan_key, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_task_runs_run_status ON operations.price_tasks (run_id, status);

CREATE INDEX IF NOT EXISTS idx_task_runs_store_time ON operations.price_tasks (store_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_alerts_open ON governance.system_alerts (created_at DESC) WHERE resolved_at IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_source_store_url ON ingestion.discovery_sources (store_id, normalized_url);

CREATE INDEX IF NOT EXISTS idx_catalog_sources_due ON ingestion.discovery_sources (enabled, next_scan_at, priority);

CREATE INDEX IF NOT EXISTS idx_catalog_tasks_run_status ON ingestion.discovery_tasks (run_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_candidate_store_url ON ingestion.discovery_candidates (store_id, normalized_url);

CREATE INDEX IF NOT EXISTS idx_catalog_candidates_status ON ingestion.discovery_candidates (status, last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_catalog_candidates_gtin ON ingestion.discovery_candidates (gtin) WHERE gtin IS NOT NULL AND gtin <> '';

CREATE INDEX IF NOT EXISTS idx_catalog_candidates_fingerprint ON ingestion.discovery_candidates (fingerprint) WHERE fingerprint IS NOT NULL AND fingerprint <> '';

CREATE INDEX IF NOT EXISTS idx_product_variants_public_source_status ON catalog.variants (source_status);

CREATE INDEX IF NOT EXISTS idx_categories_parent_active ON catalog.categories (parent_category_id, active, sort_order, name_ar);

CREATE INDEX IF NOT EXISTS idx_brands_name_trgm ON catalog.brands USING gin (name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_product_models_catalog ON catalog.products (category_id, brand_id, active, canonical_name);

CREATE INDEX IF NOT EXISTS idx_product_models_name_trgm ON catalog.products USING gin (canonical_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_product_variants_product ON catalog.variants (product_id, active);

CREATE INDEX IF NOT EXISTS idx_product_variants_taxonomy ON catalog.variants (category_id, brand_id, active);

CREATE UNIQUE INDEX IF NOT EXISTS uq_open_review_issue ON governance.review_cases (entity_type, entity_id, issue_code) WHERE status IN ('open', 'in_review');

CREATE INDEX IF NOT EXISTS idx_review_queue_status_severity ON governance.review_cases (status, severity, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_log_entity_time ON governance.audit_events (entity_type, entity_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_log_actor_time ON governance.audit_events (actor, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_product_search_alias ON catalog.search_aliases ((COALESCE(variant_id, '')), (COALESCE(product_id, '')), normalized_alias);

CREATE INDEX IF NOT EXISTS idx_product_search_alias_trgm ON catalog.search_aliases USING gin (normalized_alias gin_trgm_ops) WHERE active;

CREATE INDEX IF NOT EXISTS idx_product_variants_search_document_trgm ON catalog.variants USING gin (search_document gin_trgm_ops) WHERE active;

CREATE INDEX IF NOT EXISTS idx_price_alert_rules_active ON pricing.alert_rules (variant_id, rule_type, active, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_price_alert_rules_digest ON pricing.alert_rules (channel, rule_type, active) WHERE active;

CREATE INDEX IF NOT EXISTS idx_price_reports_open ON pricing.price_reports (status, created_at DESC) WHERE status IN ('open', 'triaged');

CREATE INDEX IF NOT EXISTS idx_price_reports_entity ON pricing.price_reports (variant_id, store_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_comparison_shares_expiry ON pricing.comparison_shares (expires_at);

CREATE INDEX IF NOT EXISTS idx_cash_visible_store_variant ON pricing.current_offers (store_id, variant_id, last_success_at DESC) WHERE active AND cash_price IS NOT NULL AND cash_price >= 10 AND anomaly_status <> 'blocked';

CREATE INDEX IF NOT EXISTS idx_mappings_direct_url_coverage ON merchant.listings (store_id, direct_url_status) WHERE active;

CREATE INDEX IF NOT EXISTS idx_catalog_import_runs_store_time ON ingestion.import_runs (store_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_catalog_import_items_status ON ingestion.import_items (store_id, validation_status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_catalog_import_items_gtin ON ingestion.import_items (gtin) WHERE NULLIF(gtin, '') IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_catalog_import_items_manufacturer_sku ON ingestion.import_items (manufacturer_sku) WHERE NULLIF(manufacturer_sku, '') IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_catalog_import_items_url ON ingestion.import_items (store_id, normalized_url) WHERE NULLIF(normalized_url, '') IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_catalog_entities_status_evidence ON ingestion.identity_clusters (status, evidence_store_count DESC, last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_catalog_entities_gtin ON ingestion.identity_clusters (gtin) WHERE NULLIF(gtin, '') IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_catalog_entities_brand_sku ON ingestion.identity_clusters (normalized_brand, manufacturer_sku) WHERE NULLIF(normalized_brand, '') IS NOT NULL AND NULLIF(manufacturer_sku, '') IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_catalog_observations_entity_store ON ingestion.catalog_observations (entity_id, store_id, last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_catalog_observations_publishable ON ingestion.catalog_observations (publishable, entity_id) WHERE publishable;

CREATE INDEX IF NOT EXISTS idx_catalog_observations_store_url ON ingestion.catalog_observations (store_id, normalized_url);

CREATE INDEX IF NOT EXISTS idx_catalog_candidates_entity ON ingestion.discovery_candidates (entity_id) WHERE entity_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_catalog_import_items_entity ON ingestion.import_items (entity_id) WHERE entity_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_catalog_tasks_recovery ON ingestion.discovery_tasks (recovery_after, status) WHERE status NOT IN ('success', 'failed');

CREATE UNIQUE INDEX IF NOT EXISTS uq_catalog_delivery_queue_name ON ingestion.task_deliveries (queue_task_name) WHERE queue_task_name IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_catalog_deliveries_status_time ON ingestion.task_deliveries (status, updated_at);

CREATE INDEX IF NOT EXISTS idx_catalog_candidates_reconcile_due ON ingestion.discovery_candidates (reconcile_version, candidate_id) WHERE status IN ('pending_match', 'needs_review');

CREATE INDEX idx_products_family_active ON catalog.products (family_id, active, canonical_name);

CREATE UNIQUE INDEX uq_variant_primary_identifier ON catalog.variant_identifiers (variant_id, identifier_type) WHERE is_primary;

CREATE INDEX idx_variant_attribute_filter_text ON catalog.variant_attribute_values (attribute_id, normalized_text, variant_id) WHERE value_text IS NOT NULL;

CREATE INDEX idx_variant_attribute_filter_number ON catalog.variant_attribute_values (attribute_id, value_number, variant_id) WHERE value_number IS NOT NULL;

CREATE UNIQUE INDEX uq_listing_primary_url ON merchant.listing_urls (listing_id) WHERE is_primary;

CREATE INDEX idx_listing_urls_status ON merchant.listing_urls (status, last_verified_at, listing_id);

CREATE INDEX idx_offer_observations_listing_time ON pricing.offer_observations (listing_id, observed_at DESC);

CREATE INDEX idx_offer_observations_time_brin ON pricing.offer_observations USING brin (observed_at);

CREATE INDEX idx_raw_documents_store_time ON ingestion.raw_documents (store_id, fetched_at DESC);

CREATE INDEX idx_outbox_pending ON governance.outbox_events (occurred_at) WHERE published_at IS NULL;

CREATE INDEX idx_popularity_rank ON analytics.popularity_scores (subject_type, country_code, window_days, score DESC, subject_id);

-- ---------------------------------------------------------------------------
-- Functions
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION governance.sa3arly_audit_row_change() RETURNS trigger AS $$
DECLARE
    row_id TEXT;
    actor_name TEXT;
    request_identifier TEXT;
BEGIN
    actor_name := NULLIF(CURRENT_SETTING('app.actor', TRUE), '');
    request_identifier := NULLIF(CURRENT_SETTING('app.request_id', TRUE), '');
    IF TG_OP = 'DELETE' THEN
        row_id := TO_JSONB(OLD) ->> TG_ARGV[0];
        INSERT INTO governance.audit_events (
            entity_type, entity_id, action, actor, request_id, before_data
        ) VALUES (
            TG_TABLE_NAME, row_id, LOWER(TG_OP), actor_name, request_identifier, TO_JSONB(OLD)
        );
        RETURN OLD;
    END IF;

    row_id := TO_JSONB(NEW) ->> TG_ARGV[0];
    INSERT INTO governance.audit_events (
        entity_type, entity_id, action, actor, request_id, before_data, after_data
    ) VALUES (
        TG_TABLE_NAME,
        row_id,
        LOWER(TG_OP),
        actor_name,
        request_identifier,
        CASE WHEN TG_OP = 'UPDATE' THEN TO_JSONB(OLD) ELSE NULL END,
        TO_JSONB(NEW)
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION catalog.sa3arly_build_search_document() RETURNS trigger AS $$
BEGIN
    NEW.search_document := LOWER(REGEXP_REPLACE(CONCAT_WS(
        ' ',
        NEW.canonical_name,
        NEW.brand,
        NEW.model,
        NEW.variant_name,
        NEW.manufacturer_sku,
        NEW.gtin,
        CASE WHEN NEW.ram_gb IS NOT NULL THEN NEW.ram_gb::TEXT || ' gb ram' END,
        CASE WHEN NEW.storage_gb IS NOT NULL THEN NEW.storage_gb::TEXT || ' gb storage' END,
        ARRAY_TO_STRING(NEW.search_aliases, ' '),
        NEW.specs::TEXT
    ), '[[:space:]]+', ' ', 'g'));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION pricing.sa3arly_cash_offer_guard() RETURNS trigger AS $$
DECLARE
    market_median NUMERIC;
    reasons JSONB := '[]'::jsonb;
    mapping_confidence TEXT;
BEGIN
    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (
        ORDER BY COALESCE(h.total_price, h.cash_price + COALESCE(h.shipping_cost, 0), h.cash_price)
    )
    INTO market_median
    FROM pricing.offer_observations h
    WHERE h.variant_id = NEW.variant_id
      AND h.observed_at >= NOW() - INTERVAL '90 days'
      AND COALESCE(h.total_price, h.cash_price) >= 10;

    SELECT LOWER(COALESCE(match_confidence, ''))
    INTO mapping_confidence
    FROM merchant.listings
    WHERE mapping_id = NEW.mapping_id;

    NEW.match_quality_score := COALESCE(
        NEW.match_quality_score,
        CASE mapping_confidence
            WHEN 'high' THEN 95
            WHEN 'medium' THEN 72
            WHEN 'low' THEN 38
            WHEN 'ambiguous' THEN 20
            ELSE 55
        END
    );

    IF UPPER(COALESCE(NEW.currency, 'EGP')) = 'EGP'
       AND NEW.cash_price IS NOT NULL
       AND NEW.cash_price < 10 THEN
        reasons := reasons || JSONB_BUILD_ARRAY('below_public_price_floor');
        NEW.anomaly_status := 'blocked';
        NEW.active := FALSE;
        NEW.review_status := 'needs_review';
    ELSIF market_median IS NOT NULL AND NEW.cash_price IS NOT NULL THEN
        IF NEW.cash_price < market_median * 0.25 OR NEW.cash_price > market_median * 4 THEN
            reasons := reasons || JSONB_BUILD_ARRAY('extreme_market_outlier');
            NEW.anomaly_status := 'blocked';
            NEW.active := FALSE;
            NEW.review_status := 'needs_review';
        ELSIF NEW.cash_price < market_median * 0.55 OR NEW.cash_price > market_median * 2 THEN
            reasons := reasons || JSONB_BUILD_ARRAY('market_outlier');
            NEW.anomaly_status := 'review';
            NEW.review_status := COALESCE(NEW.review_status, 'needs_review');
        END IF;
    END IF;

    IF mapping_confidence IN ('low', 'ambiguous') THEN
        reasons := reasons || JSONB_BUILD_ARRAY('weak_variant_match');
        NEW.anomaly_status := CASE
            WHEN NEW.anomaly_status = 'blocked' THEN 'blocked'
            ELSE 'review'
        END;
        NEW.review_status := COALESCE(NEW.review_status, 'needs_review');
    END IF;

    IF LOWER(COALESCE(NEW.availability, '')) IN ('out_of_stock', 'unavailable') THEN
        reasons := reasons || JSONB_BUILD_ARRAY('not_currently_available');
    END IF;

    NEW.anomaly_reasons := COALESCE(NEW.anomaly_reasons, '[]'::jsonb) || reasons;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION pricing.sa3arly_installment_plan_guard() RETURNS trigger AS $$
DECLARE
    expected_total NUMERIC;
    reasons JSONB := '[]'::jsonb;
BEGIN
    expected_total := COALESCE(NEW.down_payment, 0)
        + COALESCE(NEW.periodic_payment, 0) * COALESCE(NEW.months, 0)
        + COALESCE(NEW.admin_fees, 0)
        + COALESCE(NEW.processing_fees, 0)
        + COALESCE(NEW.insurance_fees, 0)
        + COALESCE(NEW.other_fees, 0)
        + COALESCE(NEW.card_fees, 0)
        + COALESCE(NEW.shipping_cost, 0)
        - COALESCE(NEW.coupon_discount, 0);

    IF NEW.total_calculated IS NULL AND expected_total > 0 THEN
        NEW.total_calculated := expected_total;
    END IF;

    IF COALESCE(NEW.total_published, NEW.total_calculated, expected_total) < COALESCE(NEW.down_payment, 0) THEN
        reasons := reasons || JSONB_BUILD_ARRAY('installment_total_below_down_payment');
        NEW.anomaly_status := 'blocked';
        NEW.active := FALSE;
        NEW.review_status := 'needs_review';
    ELSIF NEW.months IS NOT NULL AND NEW.periodic_payment IS NOT NULL
          AND COALESCE(NEW.total_published, NEW.total_calculated, expected_total)
              < (NEW.periodic_payment * NEW.months * 0.8) THEN
        reasons := reasons || JSONB_BUILD_ARRAY('installment_total_inconsistent');
        NEW.anomaly_status := 'review';
        NEW.review_status := COALESCE(NEW.review_status, 'needs_review');
    END IF;

    NEW.anomaly_reasons := COALESCE(NEW.anomaly_reasons, '[]'::jsonb) || reasons;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION merchant.sa3arly_sync_listing_urls() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    selected_url TEXT;
    selected_status TEXT;
BEGIN
    selected_url := COALESCE(NULLIF(NEW.direct_product_url, ''), NEW.normalized_url, NEW.source_url);
    IF selected_url IS NULL OR BTRIM(selected_url) = '' THEN
        RETURN NEW;
    END IF;
    selected_status := CASE
        WHEN NEW.direct_url_status = 'verified' THEN 'verified'
        WHEN NEW.direct_url_status = 'failed' THEN 'broken'
        ELSE 'unverified'
    END;

    UPDATE merchant.listing_urls
    SET is_primary = FALSE
    WHERE listing_id = NEW.mapping_id
      AND normalized_url <> selected_url
      AND is_primary;

    INSERT INTO merchant.listing_urls (
        listing_id, url, normalized_url, url_kind, status, is_primary,
        last_seen_at, last_verified_at, metadata
    )
    VALUES (
        NEW.mapping_id, selected_url, selected_url, 'product', selected_status, TRUE,
        NOW(), CASE WHEN selected_status = 'verified' THEN NOW() ELSE NULL END,
        COALESCE(NEW.direct_url_evidence, '{}'::jsonb)
    )
    ON CONFLICT (listing_id, url_hash) DO UPDATE SET
        url = EXCLUDED.url,
        status = EXCLUDED.status,
        is_primary = TRUE,
        last_seen_at = NOW(),
        last_verified_at = COALESCE(EXCLUDED.last_verified_at, merchant.listing_urls.last_verified_at),
        metadata = merchant.listing_urls.metadata || EXCLUDED.metadata;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION pricing.sa3arly_prepare_offer_observation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    current_row pricing.current_offers%ROWTYPE;
BEGIN
    SELECT * INTO current_row
    FROM pricing.current_offers
    WHERE offer_key = NEW.offer_key;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Offer observation requires an existing current offer: %', NEW.offer_key;
    END IF;
    NEW.listing_id := COALESCE(NEW.listing_id, current_row.mapping_id);
    NEW.currency := COALESCE(NEW.currency, current_row.currency);
    NEW.mandatory_fees := COALESCE(NEW.mandatory_fees, current_row.mandatory_fees);
    NEW.card_fees := COALESCE(NEW.card_fees, current_row.card_fees);
    NEW.coupon_discount := COALESCE(NEW.coupon_discount, current_row.coupon_discount);
    NEW.quality_status := CASE
        WHEN current_row.anomaly_status = 'blocked' THEN 'blocked'
        WHEN current_row.anomaly_status = 'clear' THEN 'verified'
        ELSE 'review'
    END;
    NEW.quality_reasons := COALESCE(current_row.anomaly_reasons, '[]'::jsonb);
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION pricing.sa3arly_promote_current_observation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE pricing.current_offers
    SET current_observation_id = NEW.history_id,
        updated_at = GREATEST(updated_at, NEW.observed_at)
    WHERE offer_key = NEW.offer_key;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION governance.sa3arly_reject_ledger_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME;
END;
$$;

-- ---------------------------------------------------------------------------
-- Triggers
-- ---------------------------------------------------------------------------

CREATE TRIGGER trg_audit_product_variants AFTER INSERT OR DELETE OR UPDATE ON catalog.variants FOR EACH ROW EXECUTE PROCEDURE governance.sa3arly_audit_row_change('variant_id');

CREATE TRIGGER trg_audit_store_product_mappings AFTER INSERT OR DELETE OR UPDATE ON merchant.listings FOR EACH ROW EXECUTE PROCEDURE governance.sa3arly_audit_row_change('mapping_id');

CREATE TRIGGER trg_audit_product_models AFTER INSERT OR DELETE OR UPDATE ON catalog.products FOR EACH ROW EXECUTE PROCEDURE governance.sa3arly_audit_row_change('product_id');

CREATE TRIGGER trg_product_variants_search_document BEFORE INSERT OR UPDATE OF canonical_name, brand, model, variant_name, manufacturer_sku, gtin, ram_gb, storage_gb, specs, search_aliases ON catalog.variants FOR EACH ROW EXECUTE PROCEDURE catalog.sa3arly_build_search_document();

CREATE TRIGGER trg_cash_offer_guard BEFORE INSERT OR UPDATE OF cash_price, total_price, shipping_cost, availability, mapping_id, variant_id, active ON pricing.current_offers FOR EACH ROW EXECUTE PROCEDURE pricing.sa3arly_cash_offer_guard();

CREATE TRIGGER trg_installment_plan_guard BEFORE INSERT OR UPDATE OF periodic_payment, months, down_payment, admin_fees, processing_fees, insurance_fees, other_fees, total_published, total_calculated, card_fees, shipping_cost, coupon_discount, active ON pricing.current_installment_offers FOR EACH ROW EXECUTE PROCEDURE pricing.sa3arly_installment_plan_guard();

CREATE TRIGGER trg_sync_listing_urls AFTER INSERT OR UPDATE OF source_url, normalized_url, direct_product_url, direct_url_status ON merchant.listings FOR EACH ROW EXECUTE PROCEDURE merchant.sa3arly_sync_listing_urls();

CREATE TRIGGER trg_prepare_offer_observation BEFORE INSERT ON pricing.offer_observations FOR EACH ROW EXECUTE PROCEDURE pricing.sa3arly_prepare_offer_observation();

CREATE TRIGGER trg_promote_current_observation AFTER INSERT ON pricing.offer_observations FOR EACH ROW EXECUTE PROCEDURE pricing.sa3arly_promote_current_observation();

CREATE TRIGGER trg_offer_observations_append_only BEFORE DELETE OR UPDATE ON pricing.offer_observations FOR EACH ROW EXECUTE PROCEDURE governance.sa3arly_reject_ledger_mutation();

CREATE TRIGGER trg_installment_observations_append_only BEFORE DELETE OR UPDATE ON pricing.installment_observations FOR EACH ROW EXECUTE PROCEDURE governance.sa3arly_reject_ledger_mutation();

-- ---------------------------------------------------------------------------
-- Serving views
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW pricing.public_cash_offers AS SELECT o.offer_id, o.offer_key, o.variant_id, o.store_id, o.seller_id, o.seller_name, o.currency, o.cash_price, o.old_price, o.discount_amount, o.discount_percent, o.shipping_cost, o.total_price, o.free_shipping, o.availability, o.available_quantity, o.purchase_limit, o.delivery_region, o.delivery_text, o.min_delivery_days, o.max_delivery_days, o.warranty_type, o.warranty_provider, o.warranty_months, o.store_verified, o.seller_verified, o.source_url, o.last_checked_at, o.last_success_at, p.canonical_name, p.section, p.product_type, p.brand, p.model, p.variant_name, p.ram_gb, p.storage_gb, p.color, s.name AS store_name, s.base_url AS store_base_url, CASE WHEN o.last_success_at IS NULL THEN 'unseen' WHEN o.last_success_at >= (now() - CAST('735 minutes' AS interval)) THEN 'fresh' WHEN o.last_success_at >= (now() - CAST('1470 minutes' AS interval)) THEN 'late' ELSE 'stale' END AS computed_freshness, o.last_success_at IS NOT NULL AND o.last_success_at >= (now() - CAST('1470 minutes' AS interval)) AND o.cash_price IS NOT NULL AND o.cash_price >= 10 AND o.currency = 'EGP' AND o.extraction_status = 'success' AND COALESCE(o.availability, 'unknown') <> 'out_of_stock' AND COALESCE(o.review_status, '') <> 'مرفوض' AND o.anomaly_status = 'clear' AS eligible_for_ranking, COALESCE(o.free_shipping, FALSE) OR o.shipping_cost IS NOT NULL OR o.total_price IS NOT NULL AS shipping_cost_known, CASE WHEN o.total_price IS NOT NULL THEN o.total_price WHEN o.free_shipping THEN o.cash_price WHEN o.shipping_cost IS NOT NULL THEN o.cash_price + o.shipping_cost ELSE NULL END AS comparable_total, o.anomaly_status, o.anomaly_reasons FROM pricing.current_offers AS o INNER JOIN catalog.variants AS p ON p.variant_id = o.variant_id INNER JOIN merchant.stores AS s ON s.store_id = o.store_id WHERE o.active = TRUE AND o.cash_price >= 10 AND o.anomaly_status <> 'blocked';

CREATE OR REPLACE VIEW pricing.public_installment_offers AS SELECT i.plan_id, i.plan_key, i.variant_id, i.store_id, i.seller_id, i.seller_name, i.provider_id, i.provider_name, i.provider_type, i.bank_or_card, i.plan_name, i.months, i.payment_frequency, i.periodic_payment, i.first_payment, i.down_payment, i.down_payment_percent, i.admin_fees, i.processing_fees, i.insurance_fees, i.other_fees, i.total_published, i.total_calculated, i.cash_price_at_observation, i.financing_cost, i.financing_markup_percent, i.apr, i.interest_type, i.interest_free, i.grace_months, i.minimum_purchase, i.maximum_financing, i.eligibility, i.required_card, i.customer_type, i.new_customers_only, i.geography, i.starts_at, i.ends_at, i.promo_code, i.terms_url, i.source_url, i.starting_from_only, i.completeness, i.last_checked_at, i.last_success_at, p.canonical_name, p.section, p.product_type, p.brand, p.model, p.variant_name, s.name AS store_name, s.base_url AS store_base_url, o.availability AS cash_availability, o.currency AS currency, CASE WHEN i.last_success_at IS NULL THEN 'unseen' WHEN i.last_success_at >= (now() - CAST('735 minutes' AS interval)) THEN 'fresh' WHEN i.last_success_at >= (now() - CAST('1470 minutes' AS interval)) THEN 'late' ELSE 'stale' END AS computed_freshness, COALESCE(i.total_published, i.total_calculated) AS normalized_total, i.last_success_at IS NOT NULL AND i.last_success_at >= (now() - CAST('1470 minutes' AS interval)) AND i.extraction_status = 'success' AND (i.periodic_payment IS NOT NULL OR i.total_published IS NOT NULL OR i.total_calculated IS NOT NULL) AND COALESCE(o.availability, 'unknown') <> 'out_of_stock' AND (i.starts_at IS NULL OR i.starts_at <= now()) AND (i.ends_at IS NULL OR i.ends_at >= now()) AND i.starting_from_only = FALSE AND i.completeness = 'complete' AND i.months IS NOT NULL AND i.months > 0 AND COALESCE(i.provider_name, i.bank_or_card, i.plan_name) IS NOT NULL AND COALESCE(i.review_status, '') <> 'مرفوض' AS eligible_for_ranking FROM pricing.current_installment_offers AS i INNER JOIN catalog.variants AS p ON p.variant_id = i.variant_id INNER JOIN merchant.stores AS s ON s.store_id = i.store_id LEFT JOIN pricing.current_offers AS o ON o.offer_key = i.cash_offer_key WHERE i.active = TRUE;

CREATE OR REPLACE VIEW pricing.offer_summary AS WITH cash AS (SELECT o.variant_id, min(o.cash_price) AS lowest_cash_price, min(CASE WHEN o.total_price IS NOT NULL THEN o.total_price WHEN o.free_shipping THEN o.cash_price WHEN o.shipping_cost IS NOT NULL THEN o.cash_price + o.shipping_cost ELSE NULL END) AS lowest_delivered_total, count(DISTINCT o.offer_key) AS cash_offer_count FROM pricing.current_offers AS o WHERE o.cash_price IS NOT NULL AND o.active = TRUE AND o.currency = 'EGP' AND o.last_success_at >= (now() - CAST('1470 minutes' AS interval)) AND o.extraction_status = 'success' AND COALESCE(o.availability, 'unknown') <> 'out_of_stock' AND COALESCE(o.review_status, '') <> 'مرفوض' GROUP BY o.variant_id), installment AS (SELECT i.variant_id, count(DISTINCT i.plan_key) AS installment_plan_count, min(i.periodic_payment) FILTER (WHERE i.periodic_payment IS NOT NULL AND i.starting_from_only = FALSE) AS lowest_periodic_payment FROM pricing.current_installment_offers AS i LEFT JOIN pricing.current_offers AS o ON o.offer_key = i.cash_offer_key WHERE i.active = TRUE AND i.last_success_at >= (now() - CAST('1470 minutes' AS interval)) AND i.extraction_status = 'success' AND (i.periodic_payment IS NOT NULL OR i.total_published IS NOT NULL OR i.total_calculated IS NOT NULL) AND COALESCE(o.availability, 'unknown') <> 'out_of_stock' AND (i.starts_at IS NULL OR i.starts_at <= now()) AND (i.ends_at IS NULL OR i.ends_at >= now()) AND i.starting_from_only = FALSE AND i.completeness = 'complete' AND i.months IS NOT NULL AND i.months > 0 AND COALESCE(i.provider_name, i.bank_or_card, i.plan_name) IS NOT NULL AND COALESCE(i.review_status, '') <> 'مرفوض' GROUP BY i.variant_id) SELECT p.variant_id, p.canonical_name, p.section, p.product_type, p.brand, p.model, p.variant_name, c.lowest_cash_price, c.lowest_delivered_total, c.lowest_delivered_total AS lowest_cash_total, COALESCE(c.cash_offer_count, 0) AS cash_offer_count, COALESCE(i.installment_plan_count, 0) AS installment_plan_count, i.lowest_periodic_payment FROM catalog.variants AS p LEFT JOIN cash AS c ON c.variant_id = p.variant_id LEFT JOIN installment AS i ON i.variant_id = p.variant_id;

CREATE OR REPLACE VIEW catalog.public_products AS WITH variant_rollup AS (SELECT product_id, count(*) AS variant_count, max(updated_at) AS updated_at FROM catalog.variants WHERE active AND product_id IS NOT NULL GROUP BY product_id), mapping_rollup AS (SELECT v.product_id, count(DISTINCT m.store_id) AS connected_store_count FROM catalog.variants AS v INNER JOIN merchant.listings AS m ON m.variant_id = v.variant_id AND m.active WHERE v.active AND v.product_id IS NOT NULL GROUP BY v.product_id), offer_rollup AS (SELECT v.product_id, min(s.lowest_cash_price) AS lowest_cash_price, min(s.lowest_delivered_total) AS lowest_delivered_total, sum(COALESCE(s.cash_offer_count, 0)) AS cash_offer_count, sum(COALESCE(s.installment_plan_count, 0)) AS installment_plan_count FROM catalog.variants AS v LEFT JOIN pricing.offer_summary AS s ON s.variant_id = v.variant_id WHERE v.active AND v.product_id IS NOT NULL GROUP BY v.product_id) SELECT pm.product_id, pm.canonical_name AS product_name, pm.model, pm.source_status, pm.active, b.brand_id, b.name AS brand_name, c.category_id, c.name_ar AS category_name, parent.category_id AS parent_category_id, parent.name_ar AS parent_category_name, COALESCE(vr.variant_count, 0) AS variant_count, COALESCE(mr.connected_store_count, 0) AS connected_store_count, oru.lowest_cash_price, oru.lowest_delivered_total, COALESCE(oru.cash_offer_count, 0) AS cash_offer_count, COALESCE(oru.installment_plan_count, 0) AS installment_plan_count, COALESCE(vr.updated_at, pm.updated_at) AS updated_at FROM catalog.products AS pm LEFT JOIN catalog.brands AS b ON b.brand_id = pm.brand_id LEFT JOIN catalog.categories AS c ON c.category_id = pm.category_id LEFT JOIN catalog.categories AS parent ON parent.category_id = c.parent_category_id LEFT JOIN variant_rollup AS vr ON vr.product_id = pm.product_id LEFT JOIN mapping_rollup AS mr ON mr.product_id = pm.product_id LEFT JOIN offer_rollup AS oru ON oru.product_id = pm.product_id;

CREATE OR REPLACE VIEW governance.data_quality_summary AS SELECT (SELECT count(*) FROM catalog.products WHERE active) AS products, (SELECT count(*) FROM catalog.variants WHERE active) AS variants, (SELECT count(*) FROM merchant.stores WHERE active) AS active_stores, (SELECT count(*) FROM merchant.listings WHERE active) AS active_mappings, (SELECT count(*) FROM pricing.current_offers WHERE active AND cash_price IS NOT NULL) AS cash_offers, (SELECT count(*) FROM pricing.current_installment_offers WHERE active) AS installment_plans, (SELECT count(*) FROM governance.review_cases WHERE status = 'open') AS open_reviews, (SELECT count(*) FROM governance.review_cases WHERE status = 'open' AND severity IN ('high', 'critical')) AS urgent_reviews, (SELECT count(*) FROM ingestion.discovery_candidates WHERE status = 'needs_review') AS catalog_needs_review, (SELECT count(*) FROM catalog.variants WHERE source_status = 'catalog_provisional') AS provisional_variants, (SELECT count(*) FROM merchant.listings WHERE active AND lower(COALESCE(match_confidence, '')) IN ('low', 'medium', 'ambiguous')) AS weak_mappings, (SELECT max(last_success_at) FROM pricing.current_offers) AS latest_cash_update, (SELECT max(last_success_at) FROM pricing.current_installment_offers) AS latest_installment_update;

CREATE OR REPLACE VIEW pricing.cash_decision_inputs AS SELECT o.offer_id, o.variant_id, o.store_id, s.name AS store_name, o.seller_id, o.seller_name, o.currency, o.cash_price, o.shipping_cost, o.mandatory_fees, o.card_fees, o.coupon_code, o.coupon_discount, CASE WHEN o.cash_price IS NULL THEN NULL ELSE GREATEST((o.cash_price + COALESCE(o.shipping_cost, 0) + COALESCE(o.mandatory_fees, 0) + COALESCE(o.card_fees, 0)) - COALESCE(o.coupon_discount, 0), 0) END AS final_cost, o.availability, o.pickup_available, o.min_delivery_days, o.max_delivery_days, o.warranty_type, o.warranty_provider, o.warranty_months, o.store_verified, o.seller_verified, o.last_success_at, o.match_quality_score, o.anomaly_status, o.anomaly_reasons, o.source_url, m.mapping_id, m.store_sku, m.manufacturer_sku AS mapping_manufacturer_sku, m.title_as_seen, m.match_confidence, m.review_status AS mapping_review_status, sqm.price_accuracy_score, sqm.update_regularity_score, sqm.availability_clarity_score, sqm.warranty_clarity_score, sqm.correct_destination_score, sqm.broken_link_rate, sqm.complaint_response_score, sqm.sample_size AS store_quality_sample_size FROM pricing.current_offers AS o INNER JOIN merchant.stores AS s ON s.store_id = o.store_id INNER JOIN merchant.listings AS m ON m.mapping_id = o.mapping_id LEFT JOIN merchant.store_quality_metrics AS sqm ON sqm.store_id = o.store_id WHERE o.active AND o.cash_price IS NOT NULL AND o.anomaly_status <> 'blocked';

CREATE OR REPLACE VIEW pricing.installment_decision_inputs AS SELECT p.*, s.name AS store_name, CASE WHEN COALESCE(p.total_published, p.total_calculated) IS NOT NULL THEN COALESCE(p.total_published, p.total_calculated) WHEN p.periodic_payment IS NOT NULL AND p.months IS NOT NULL THEN GREATEST((COALESCE(p.down_payment, 0) + (p.periodic_payment * p.months) + COALESCE(p.admin_fees, 0) + COALESCE(p.processing_fees, 0) + COALESCE(p.insurance_fees, 0) + COALESCE(p.other_fees, 0) + COALESCE(p.card_fees, 0) + COALESCE(p.shipping_cost, 0)) - COALESCE(p.coupon_discount, 0), 0) ELSE NULL END AS final_installment_cost, sqm.price_accuracy_score, sqm.update_regularity_score, sqm.availability_clarity_score, sqm.warranty_clarity_score, sqm.correct_destination_score, sqm.broken_link_rate, sqm.sample_size AS store_quality_sample_size FROM pricing.current_installment_offers AS p INNER JOIN merchant.stores AS s ON s.store_id = p.store_id LEFT JOIN merchant.store_quality_metrics AS sqm ON sqm.store_id = p.store_id WHERE p.active AND p.anomaly_status <> 'blocked';

CREATE OR REPLACE VIEW merchant.direct_link_coverage AS WITH mapping_coverage AS (SELECT m.store_id, count(*) FILTER (WHERE m.active) AS active_mappings, count(*) FILTER (WHERE m.active AND NULLIF(m.direct_product_url, '') IS NOT NULL) AS mappings_with_direct_url, count(*) FILTER (WHERE m.active AND m.direct_url_status = 'verified') AS verified_direct_urls, count(*) FILTER (WHERE m.active AND m.direct_url_status = 'legacy_unverified') AS legacy_unverified_urls, count(*) FILTER (WHERE m.active AND m.direct_url_status IN ('failed', 'conflict')) AS failed_or_conflicting_urls, max(m.direct_url_verified_at) AS latest_direct_url_verification FROM merchant.listings AS m GROUP BY m.store_id), import_coverage AS (SELECT store_id, max(completed_at) AS latest_catalog_import FROM ingestion.import_runs GROUP BY store_id) SELECT s.store_id, s.name AS store_name, COALESCE(m.active_mappings, 0) AS active_mappings, COALESCE(m.mappings_with_direct_url, 0) AS mappings_with_direct_url, COALESCE(m.verified_direct_urls, 0) AS verified_direct_urls, COALESCE(m.legacy_unverified_urls, 0) AS legacy_unverified_urls, COALESCE(m.failed_or_conflicting_urls, 0) AS failed_or_conflicting_urls, round((100.0 * COALESCE(m.verified_direct_urls, 0)) / (NULLIF(COALESCE(m.active_mappings, 0), 0)), 2) AS verified_direct_url_percent, m.latest_direct_url_verification, r.latest_catalog_import FROM merchant.stores AS s LEFT JOIN mapping_coverage AS m ON m.store_id = s.store_id LEFT JOIN import_coverage AS r ON r.store_id = s.store_id;

CREATE OR REPLACE VIEW merchant.coverage_ledger AS WITH mapping_stats AS (SELECT store_id, count(*) FILTER (WHERE active) AS active_mappings, count(*) FILTER (WHERE active AND direct_url_status = 'verified') AS verified_direct_urls FROM merchant.listings GROUP BY store_id), cash_stats AS (SELECT store_id, count(*) AS visible_cash_offers, count(*) FILTER (WHERE eligible_for_ranking) AS ranked_cash_offers, max(last_success_at) AS latest_cash_success FROM pricing.public_cash_offers GROUP BY store_id), discovery_stats AS (SELECT store_id, bool_or(enabled) AS discovery_configured, max(last_scan_at) AS latest_catalog_scan, max(last_success_at) AS latest_catalog_success, ((array_agg(last_error_code ORDER BY updated_at DESC) FILTER (WHERE last_error_code IS NOT NULL)))[1] AS latest_catalog_error FROM ingestion.discovery_sources GROUP BY store_id) SELECT s.store_id, s.name AS store_name, s.active, s.registry_status, s.public_price_status, s.online_purchase, COALESCE(s.registry_status, '') <> 'نشط/كتالوج فقط' AND COALESCE(s.public_price_status, '') <> 'كتالوج فقط' AND COALESCE(s.online_purchase, '') <> 'لا' AS price_capable, COALESCE(d.discovery_configured, FALSE) AS discovery_configured, d.latest_catalog_scan, d.latest_catalog_success, d.latest_catalog_error, COALESCE(m.active_mappings, 0) AS active_mappings, COALESCE(m.verified_direct_urls, 0) AS verified_direct_urls, COALESCE(c.visible_cash_offers, 0) AS visible_cash_offers, COALESCE(c.ranked_cash_offers, 0) AS ranked_cash_offers, c.latest_cash_success, CASE WHEN COALESCE(s.registry_status, '') = 'نشط/كتالوج فقط' OR COALESCE(s.public_price_status, '') = 'كتالوج فقط' OR COALESCE(s.online_purchase, '') = 'لا' THEN 'catalog_only' WHEN COALESCE(c.visible_cash_offers, 0) > 0 THEN 'live_price' WHEN COALESCE(m.active_mappings, 0) > 0 THEN 'linked_waiting_price' WHEN d.latest_catalog_success IS NOT NULL THEN 'discovered_waiting_match' WHEN d.latest_catalog_scan IS NOT NULL THEN 'discovery_failed' WHEN COALESCE(d.discovery_configured, FALSE) THEN 'pending_discovery' ELSE 'connector_missing' END AS coverage_stage FROM merchant.stores AS s LEFT JOIN mapping_stats AS m ON m.store_id = s.store_id LEFT JOIN cash_stats AS c ON c.store_id = s.store_id LEFT JOIN discovery_stats AS d ON d.store_id = s.store_id;

CREATE OR REPLACE VIEW pricing.public_offer_table AS SELECT o.variant_id, o.offer_id, o.mapping_id AS listing_id, o.store_id, s.name AS store_name, o.seller_id, o.seller_name, o.currency, o.cash_price, o.shipping_cost, o.mandatory_fees, o.card_fees, o.coupon_discount, o.final_cost, o.availability, o.min_delivery_days, o.max_delivery_days, o.warranty_type, o.warranty_provider, o.warranty_months, o.last_success_at, u.url AS product_url, o.anomaly_status, o.anomaly_status <> 'blocked' AS visible, o.anomaly_status = 'clear' AND o.match_quality_score >= 80 AS rankable FROM pricing.current_offers AS o INNER JOIN merchant.stores AS s ON s.store_id = o.store_id AND s.active INNER JOIN merchant.listings AS l ON l.mapping_id = o.mapping_id AND l.active LEFT JOIN merchant.listing_urls AS u ON u.listing_id = l.mapping_id AND u.is_primary WHERE o.active AND o.cash_price IS NOT NULL AND o.anomaly_status <> 'blocked';

-- ---------------------------------------------------------------------------
-- Documentation
-- ---------------------------------------------------------------------------

COMMENT ON VIEW pricing.public_offer_table IS 'Single read model for the user-facing store/price/shipping comparison table.';
