from __future__ import annotations

import csv
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.db import DATABASE_SEARCH_PATH
from scripts.apply_migrations import apply_all

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"
SEED = ROOT / "db" / "seed"


def read_rows(name: str) -> list[dict[str, str]]:
    with (SEED / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def operational_mapping_ids() -> set[str]:
    return {
        row["mapping_id"]
        for row in read_rows("operational_mapping_ids.csv")
        if row.get("mapping_id")
    }


def null(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value if value else None
    return value


def boolean(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "نعم", "صح"}


def number(value: Any) -> float | None:
    value = null(value)
    if value is None:
        return None
    return float(value)


def integer(value: Any) -> int:
    value = null(value)
    return int(float(value)) if value is not None else 0


def dt(value: Any) -> datetime | None:
    value = null(value)
    if value is None:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def normalize_key(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def stable_id(prefix: str, *parts: Any, length: int = 20) -> str:
    identity = "|".join(normalize_key(part) for part in parts)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:length].upper()
    return f"{prefix}-{digest}"


PHONE_EDITION_SUFFIX = re.compile(
    r"\s+(?:pro\s+max|pro|plus|ultra|max|mini|air|lite|se)\b.*$",
    re.IGNORECASE,
)


def product_family_name(model: Any, product_type: Any) -> str:
    model_name = str(model or "").strip()
    type_name = normalize_key(product_type)
    if "هاتف" in type_name or "phone" in type_name:
        family = PHONE_EDITION_SUFFIX.sub("", model_name).strip()
        if family:
            return family
    return model_name


def execute_many(
    conn: psycopg.Connection,
    statement: str,
    payload: list[dict[str, Any]],
) -> None:
    """Execute a batch through a psycopg cursor.

    Psycopg 3 exposes ``execute()`` on connections for convenience, but batch
    ``executemany()`` belongs to cursors.
    """
    with conn.cursor() as cursor:
        cursor.executemany(statement, payload)


def import_products(conn: psycopg.Connection) -> int:
    rows = read_rows("product_variants.csv")
    categories: dict[str, dict[str, Any]] = {}
    brands: dict[str, dict[str, Any]] = {}
    families: dict[str, dict[str, Any]] = {}
    products: dict[str, dict[str, Any]] = {}
    attributes: dict[str, dict[str, Any]] = {}
    attribute_values: list[dict[str, Any]] = []
    identifiers: list[dict[str, Any]] = []
    payload: list[dict[str, Any]] = []

    for row in rows:
        section = str(row.get("section") or "غير مصنف").strip()
        product_type = str(row.get("product_type") or section).strip()
        brand_name = str(row.get("brand") or "غير محدد").strip()
        model_name = str(row.get("model") or row["canonical_name"]).strip()

        root_category_id = stable_id("CAT", "root", section, length=16)
        leaf_category_id = stable_id("CAT", "leaf", section, product_type, length=16)
        categories[root_category_id] = {
            "category_id": root_category_id,
            "parent_category_id": None,
            "source_key": f"section:{normalize_key(section)}",
            "slug": f"section-{root_category_id.lower()}",
            "name_ar": section,
            "name_en": section,
            "level": 1,
        }
        categories[leaf_category_id] = {
            "category_id": leaf_category_id,
            "parent_category_id": root_category_id if product_type != section else None,
            "source_key": f"type:{normalize_key(section)}:{normalize_key(product_type)}",
            "slug": f"type-{leaf_category_id.lower()}",
            "name_ar": product_type,
            "name_en": product_type,
            "level": 2 if product_type != section else 1,
        }

        normalized_brand = normalize_key(brand_name)
        brand_id = stable_id("BRD", normalized_brand, length=16)
        brands[brand_id] = {
            "brand_id": brand_id,
            "slug": f"brand-{brand_id.lower()}",
            "name": brand_name,
            "normalized_name": normalized_brand,
        }

        family_name = product_family_name(model_name, product_type)
        family_id = stable_id(
            "FAM", leaf_category_id, brand_id, family_name, length=18
        )
        families[family_id] = {
            "family_id": family_id,
            "category_id": leaf_category_id,
            "brand_id": brand_id,
            "canonical_name": family_name,
            "normalized_name": normalize_key(family_name),
        }

        product_id = stable_id(
            "PRD", leaf_category_id, brand_id, model_name, length=18
        )
        products[product_id] = {
            "product_id": product_id,
            "family_id": family_id,
            "category_id": leaf_category_id,
            "brand_id": brand_id,
            "canonical_name": " ".join(
                part for part in (brand_name, model_name) if part
            ),
            "model": model_name,
            "source_status": row.get("source_status") or "mapped",
        }

        ram_gb = number(row["ram_gb"])
        storage_gb = number(row["storage_gb"])
        variant = {
            **row,
            "product_id": product_id,
            "category_id": leaf_category_id,
            "brand_id": brand_id,
            "ram_gb": ram_gb,
            "storage_gb": storage_gb,
            "created_at": dt(row["created_at"]),
            "updated_at": dt(row["updated_at"]),
        }
        payload.append(variant)

        for code, name_ar, value_type, value, normalized_text, unit in (
            ("ram_gb", "الذاكرة", "number", ram_gb, None, "GB"),
            ("storage_gb", "السعة التخزينية", "number", storage_gb, None, "GB"),
            ("color", "اللون", "text", null(row.get("color")), normalize_key(row.get("color")), None),
        ):
            if value is None:
                continue
            attribute_id = stable_id("ATR", leaf_category_id, code, length=18)
            attributes[attribute_id] = {
                "attribute_id": attribute_id,
                "category_id": leaf_category_id,
                "code": code,
                "name_ar": name_ar,
                "name_en": code,
                "value_type": value_type,
                "default_unit": unit,
            }
            attribute_values.append(
                {
                    "variant_id": row["variant_id"],
                    "attribute_id": attribute_id,
                    "value_text": value if value_type == "text" else None,
                    "value_number": value if value_type == "number" else None,
                    "normalized_text": normalized_text,
                    "unit": unit,
                }
            )

        for identifier_type, identifier_value in (
            ("gtin", null(row.get("gtin"))),
            ("manufacturer_sku", null(row.get("manufacturer_sku"))),
        ):
            if identifier_value:
                identifiers.append(
                    {
                        "variant_id": row["variant_id"],
                        "identifier_type": identifier_type,
                        "identifier_value": identifier_value,
                        "normalized_value": normalize_key(identifier_value),
                    }
                )

    execute_many(
        conn,
        """
        INSERT INTO categories (
            category_id, parent_category_id, source_key, slug, name_ar, name_en, level
        ) VALUES (
            %(category_id)s, %(parent_category_id)s, %(source_key)s, %(slug)s,
            %(name_ar)s, %(name_en)s, %(level)s
        )
        ON CONFLICT (category_id) DO UPDATE SET
            parent_category_id = EXCLUDED.parent_category_id,
            name_ar = EXCLUDED.name_ar,
            name_en = EXCLUDED.name_en,
            level = EXCLUDED.level,
            updated_at = NOW()
        """,
        list(categories.values()),
    )
    execute_many(
        conn,
        """
        INSERT INTO brands (brand_id, slug, name, normalized_name)
        VALUES (%(brand_id)s, %(slug)s, %(name)s, %(normalized_name)s)
        ON CONFLICT (brand_id) DO UPDATE SET
            name = EXCLUDED.name,
            normalized_name = EXCLUDED.normalized_name,
            updated_at = NOW()
        """,
        list(brands.values()),
    )
    execute_many(
        conn,
        """
        INSERT INTO product_families (
            family_id, category_id, brand_id, canonical_name, normalized_name
        ) VALUES (
            %(family_id)s, %(category_id)s, %(brand_id)s,
            %(canonical_name)s, %(normalized_name)s
        )
        ON CONFLICT (family_id) DO UPDATE SET
            canonical_name = EXCLUDED.canonical_name,
            normalized_name = EXCLUDED.normalized_name,
            updated_at = NOW()
        """,
        list(families.values()),
    )
    execute_many(
        conn,
        """
        INSERT INTO products (
            product_id, family_id, category_id, brand_id,
            canonical_name, model, source_status
        ) VALUES (
            %(product_id)s, %(family_id)s, %(category_id)s, %(brand_id)s,
            %(canonical_name)s, %(model)s, %(source_status)s
        )
        ON CONFLICT (product_id) DO UPDATE SET
            family_id = EXCLUDED.family_id,
            category_id = EXCLUDED.category_id,
            brand_id = EXCLUDED.brand_id,
            canonical_name = EXCLUDED.canonical_name,
            model = EXCLUDED.model,
            source_status = EXCLUDED.source_status,
            updated_at = NOW()
        """,
        list(products.values()),
    )
    execute_many(
        conn,
        """
        INSERT INTO variants (
            variant_id, model_id, product_id, category_id, brand_id,
            canonical_name, section, product_type, brand, model, variant_name,
            ram_gb, storage_gb, color, manufacturer_sku, gtin,
            manufacturer_url, source_status, created_at, updated_at
        )
        VALUES (
            %(variant_id)s, %(model_id)s, %(product_id)s, %(category_id)s,
            %(brand_id)s, %(canonical_name)s, %(section)s, %(product_type)s,
            %(brand)s, %(model)s, %(variant_name)s, %(ram_gb)s,
            %(storage_gb)s, %(color)s, %(manufacturer_sku)s, %(gtin)s,
            %(manufacturer_url)s, %(source_status)s,
            COALESCE(%(created_at)s, NOW()), COALESCE(%(updated_at)s, NOW())
        )
        ON CONFLICT (variant_id) DO UPDATE SET
            model_id = EXCLUDED.model_id,
            product_id = EXCLUDED.product_id,
            category_id = EXCLUDED.category_id,
            brand_id = EXCLUDED.brand_id,
            canonical_name = EXCLUDED.canonical_name,
            section = EXCLUDED.section,
            product_type = EXCLUDED.product_type,
            brand = EXCLUDED.brand,
            model = EXCLUDED.model,
            variant_name = EXCLUDED.variant_name,
            ram_gb = EXCLUDED.ram_gb,
            storage_gb = EXCLUDED.storage_gb,
            color = EXCLUDED.color,
            manufacturer_sku = EXCLUDED.manufacturer_sku,
            gtin = EXCLUDED.gtin,
            manufacturer_url = COALESCE(NULLIF(EXCLUDED.manufacturer_url, ''), variants.manufacturer_url),
            source_status = EXCLUDED.source_status,
            updated_at = NOW()
        """,
        payload,
    )
    execute_many(
        conn,
        """
        INSERT INTO attribute_definitions (
            attribute_id, category_id, code, name_ar, name_en,
            value_type, default_unit
        ) VALUES (
            %(attribute_id)s, %(category_id)s, %(code)s, %(name_ar)s,
            %(name_en)s, %(value_type)s, %(default_unit)s
        )
        ON CONFLICT (attribute_id) DO UPDATE SET
            name_ar = EXCLUDED.name_ar,
            name_en = EXCLUDED.name_en,
            value_type = EXCLUDED.value_type,
            default_unit = EXCLUDED.default_unit,
            updated_at = NOW()
        """,
        list(attributes.values()),
    )
    execute_many(
        conn,
        """
        INSERT INTO variant_attribute_values (
            variant_id, attribute_id, value_text, value_number,
            normalized_text, unit
        ) VALUES (
            %(variant_id)s, %(attribute_id)s, %(value_text)s,
            %(value_number)s, %(normalized_text)s, %(unit)s
        )
        ON CONFLICT (variant_id, attribute_id, value_index) DO UPDATE SET
            value_text = EXCLUDED.value_text,
            value_number = EXCLUDED.value_number,
            normalized_text = EXCLUDED.normalized_text,
            unit = EXCLUDED.unit,
            updated_at = NOW()
        """,
        attribute_values,
    )
    execute_many(
        conn,
        """
        INSERT INTO variant_identifiers (
            variant_id, identifier_type, identifier_value, normalized_value,
            is_primary
        ) VALUES (
            %(variant_id)s, %(identifier_type)s, %(identifier_value)s,
            %(normalized_value)s, TRUE
        )
        ON CONFLICT DO NOTHING
        """,
        identifiers,
    )
    conn.commit()
    return len(rows)


def import_variant_aliases(conn: psycopg.Connection) -> int:
    path = SEED / "variant_aliases.csv"
    if not path.exists():
        return 0
    rows = read_rows("variant_aliases.csv")
    execute_many(
        conn,
        """
        INSERT INTO variant_aliases (
            alias_variant_id, canonical_variant_id, reason
        )
        VALUES (
            %(alias_variant_id)s, %(canonical_variant_id)s, %(reason)s
        )
        ON CONFLICT (alias_variant_id) DO UPDATE SET
            canonical_variant_id = EXCLUDED.canonical_variant_id,
            reason = EXCLUDED.reason
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def import_stores(conn: psycopg.Connection) -> int:
    rows = read_rows("stores.csv")
    payload = []
    for row in rows:
        payload.append(
            {
                **row,
                "current_mapping_count": integer(row["current_mapping_count"]),
                "ready_mapping_count": integer(row["ready_mapping_count"]),
                "active": boolean(row["active"]),
            }
        )
    execute_many(
        conn,
        """
        INSERT INTO stores (
            store_id, name, base_url, primary_category, coverage_categories,
            store_type, public_price_status, online_purchase, priority,
            verification_confidence, registry_status, integration_difficulty,
            current_mapping_count, ready_mapping_count, file_link_status, active
        )
        VALUES (
            %(store_id)s, %(name)s, %(base_url)s, %(primary_category)s,
            %(coverage_categories)s, %(store_type)s, %(public_price_status)s,
            %(online_purchase)s, %(priority)s, %(verification_confidence)s,
            %(registry_status)s, %(integration_difficulty)s,
            %(current_mapping_count)s, %(ready_mapping_count)s,
            %(file_link_status)s, %(active)s
        )
        ON CONFLICT (store_id) DO UPDATE SET
            name = EXCLUDED.name,
            base_url = EXCLUDED.base_url,
            primary_category = EXCLUDED.primary_category,
            coverage_categories = EXCLUDED.coverage_categories,
            store_type = EXCLUDED.store_type,
            public_price_status = EXCLUDED.public_price_status,
            online_purchase = EXCLUDED.online_purchase,
            priority = EXCLUDED.priority,
            verification_confidence = EXCLUDED.verification_confidence,
            registry_status = EXCLUDED.registry_status,
            integration_difficulty = EXCLUDED.integration_difficulty,
            current_mapping_count = EXCLUDED.current_mapping_count,
            ready_mapping_count = EXCLUDED.ready_mapping_count,
            file_link_status = EXCLUDED.file_link_status,
            active = EXCLUDED.active,
            updated_at = NOW()
        """,
        payload,
    )
    conn.commit()
    return len(rows)


def import_connectors(conn: psycopg.Connection) -> int:
    rows = read_rows("connector_configs.csv")
    payload = []
    for row in rows:
        payload.append(
            {
                "store_id": row["store_id"],
                "mode": row["mode"],
                "allowed_hosts": [x for x in row["allowed_hosts"].split("|") if x],
                "requests_per_minute": integer(row["requests_per_minute"]),
                "max_concurrency": integer(row["max_concurrency"]),
                "browser_required": boolean(row["browser_required"]),
                "respect_robots": boolean(row["respect_robots"]),
                "enabled": boolean(row["enabled"]),
                "version": row["version"],
                "config": Jsonb(json.loads(row["config_json"] or "{}")),
            }
        )
    execute_many(
        conn,
        """
        INSERT INTO connector_configs (
            store_id, mode, allowed_hosts, requests_per_minute, max_concurrency,
            browser_required, respect_robots, enabled, version, config
        )
        VALUES (
            %(store_id)s, %(mode)s, %(allowed_hosts)s, %(requests_per_minute)s,
            %(max_concurrency)s, %(browser_required)s, %(respect_robots)s,
            %(enabled)s, %(version)s, %(config)s
        )
        ON CONFLICT (store_id) DO UPDATE SET
            mode = EXCLUDED.mode,
            allowed_hosts = EXCLUDED.allowed_hosts,
            requests_per_minute = EXCLUDED.requests_per_minute,
            max_concurrency = EXCLUDED.max_concurrency,
            browser_required = EXCLUDED.browser_required,
            respect_robots = EXCLUDED.respect_robots,
            enabled = EXCLUDED.enabled,
            version = EXCLUDED.version,
            config = connector_configs.config || EXCLUDED.config,
            updated_at = NOW()
        """,
        payload,
    )
    conn.commit()
    return len(rows)


def import_mappings(
    conn: psycopg.Connection, approved_mapping_ids: set[str]
) -> int:
    rows = read_rows("store_product_mappings.csv")
    payload = []
    for row in rows:
        payload.append(
            {
                **row,
                "evidence_count": integer(row["evidence_count"]),
                "evidence_verified_at": dt(row["evidence_verified_at"]),
                "active": (
                    boolean(row["active"])
                    and row["mapping_id"] in approved_mapping_ids
                ),
                "created_at": dt(row["created_at"]),
                "updated_at": dt(row["updated_at"]),
            }
        )
    execute_many(
        conn,
        """
        INSERT INTO listings (
            mapping_id, offer_id, offer_key, variant_id, store_id, seller_id,
            seller_name, store_sku, source_url, normalized_url, url_type,
            title_as_seen, match_method, match_confidence, evidence_level,
            extraction_hint, evidence_urls, evidence_count, evidence_verified_at,
            active, review_status, created_at, updated_at
        )
        VALUES (
            %(mapping_id)s, %(offer_id)s, %(offer_key)s, %(variant_id)s,
            %(store_id)s, %(seller_id)s, %(seller_name)s, %(store_sku)s,
            %(source_url)s, %(source_url)s, %(url_type)s, %(title_as_seen)s,
            %(match_method)s, %(match_confidence)s, %(evidence_level)s,
            %(extraction_hint)s, %(evidence_urls)s, %(evidence_count)s,
            %(evidence_verified_at)s, %(active)s, %(review_status)s,
            COALESCE(%(created_at)s, NOW()), COALESCE(%(updated_at)s, NOW())
        )
        ON CONFLICT (mapping_id) DO UPDATE SET
            offer_id = EXCLUDED.offer_id,
            offer_key = EXCLUDED.offer_key,
            seller_id = EXCLUDED.seller_id,
            seller_name = EXCLUDED.seller_name,
            store_sku = EXCLUDED.store_sku,
            source_url = EXCLUDED.source_url,
            normalized_url = EXCLUDED.normalized_url,
            url_type = EXCLUDED.url_type,
            title_as_seen = EXCLUDED.title_as_seen,
            match_method = EXCLUDED.match_method,
            match_confidence = EXCLUDED.match_confidence,
            evidence_level = EXCLUDED.evidence_level,
            extraction_hint = EXCLUDED.extraction_hint,
            evidence_urls = EXCLUDED.evidence_urls,
            evidence_count = EXCLUDED.evidence_count,
            evidence_verified_at = EXCLUDED.evidence_verified_at,
            active = EXCLUDED.active,
            review_status = EXCLUDED.review_status,
            updated_at = NOW()
        """,
        payload,
    )
    conn.commit()
    return len(rows)


def import_cash_offers(
    conn: psycopg.Connection, approved_offer_keys: set[str]
) -> int:
    rows = read_rows("current_cash_offers.csv")
    payload = []
    for row in rows:
        payload.append(
            {
                **row,
                "cash_price": number(row["cash_price"]),
                "old_price": number(row["old_price"]),
                "discount_amount": number(row["discount_amount"]),
                "discount_percent": number(row["discount_percent"]),
                "shipping_cost": number(row["shipping_cost"]),
                "total_price": number(row["total_price"]),
                "free_shipping": boolean(row["free_shipping"]) if row["free_shipping"] else None,
                "available_quantity": number(row["available_quantity"]),
                "purchase_limit": number(row["purchase_limit"]),
                "min_delivery_days": number(row["min_delivery_days"]),
                "max_delivery_days": number(row["max_delivery_days"]),
                "warranty_months": number(row["warranty_months"]),
                "store_verified": boolean(row["store_verified"]) if row["store_verified"] else None,
                "seller_verified": boolean(row["seller_verified"]) if row["seller_verified"] else None,
                "last_checked_at": dt(row["last_checked_at"]),
                "last_success_at": dt(row["last_success_at"]),
                "consecutive_failures": integer(row["consecutive_failures"]),
                "last_run_id": null(row["last_run_id"]),
                "active": (
                    boolean(row["active"])
                    and row["offer_key"] in approved_offer_keys
                ),
                "created_at": dt(row["created_at"]),
                "updated_at": dt(row["updated_at"]),
            }
        )
    execute_many(
        conn,
        """
        INSERT INTO current_offers (
            offer_id, offer_key, mapping_id, variant_id, store_id, seller_id,
            seller_name, currency, cash_price, old_price, discount_amount,
            discount_percent, shipping_cost, total_price, free_shipping,
            availability, available_quantity, purchase_limit, delivery_region,
            delivery_text, min_delivery_days, max_delivery_days, warranty_type,
            warranty_provider, warranty_months, store_verified, seller_verified,
            source_method, source_url, last_checked_at, last_success_at,
            freshness_status, extraction_status, consecutive_failures,
            connector_version, last_run_id, active, review_status, review_notes,
            created_at, updated_at
        )
        VALUES (
            %(offer_id)s, %(offer_key)s, %(mapping_id)s, %(variant_id)s,
            %(store_id)s, %(seller_id)s, %(seller_name)s, %(currency)s,
            %(cash_price)s, %(old_price)s, %(discount_amount)s,
            %(discount_percent)s, %(shipping_cost)s, %(total_price)s,
            %(free_shipping)s, %(availability)s, %(available_quantity)s,
            %(purchase_limit)s, %(delivery_region)s, %(delivery_text)s,
            %(min_delivery_days)s, %(max_delivery_days)s, %(warranty_type)s,
            %(warranty_provider)s, %(warranty_months)s, %(store_verified)s,
            %(seller_verified)s, %(source_method)s, %(source_url)s,
            %(last_checked_at)s, %(last_success_at)s, %(freshness_status)s,
            %(extraction_status)s, %(consecutive_failures)s,
            %(connector_version)s, %(last_run_id)s, %(active)s,
            %(review_status)s, %(review_notes)s,
            COALESCE(%(created_at)s, NOW()), COALESCE(%(updated_at)s, NOW())
        )
        ON CONFLICT (offer_key) DO UPDATE SET
            mapping_id = EXCLUDED.mapping_id,
            seller_id = EXCLUDED.seller_id,
            seller_name = EXCLUDED.seller_name,
            currency = EXCLUDED.currency,
            source_method = CASE
                WHEN current_offers.last_success_at IS NULL THEN EXCLUDED.source_method
                ELSE current_offers.source_method
            END,
            source_url = CASE
                WHEN current_offers.last_success_at IS NULL THEN EXCLUDED.source_url
                ELSE current_offers.source_url
            END,
            active = EXCLUDED.active,
            review_status = CASE
                WHEN current_offers.last_checked_at IS NULL THEN EXCLUDED.review_status
                ELSE current_offers.review_status
            END,
            review_notes = CASE
                WHEN current_offers.last_checked_at IS NULL THEN EXCLUDED.review_notes
                ELSE current_offers.review_notes
            END,
            updated_at = NOW()
        """,
        payload,
    )
    conn.commit()
    return len(rows)


def import_installment_tasks(
    conn: psycopg.Connection, approved_offer_keys: set[str]
) -> int:
    rows = read_rows("installment_discovery_tasks.csv")
    payload = []
    for row in rows:
        payload.append(
            {
                **row,
                "active": (
                    boolean(row["active"])
                    and row["cash_offer_key"] in approved_offer_keys
                ),
                "evidence_verified_at": dt(row["evidence_verified_at"]),
                "created_at": dt(row["created_at"]),
                "updated_at": dt(row["updated_at"]),
            }
        )
    execute_many(
        conn,
        """
        INSERT INTO installment_tasks (
            task_id, cash_offer_key, mapping_id, variant_id, store_id, seller_id,
            source_url, url_type, status, review_status, title_as_seen, notes,
            active, evidence_verified_at, created_at, updated_at
        )
        SELECT
            %(task_id)s, %(cash_offer_key)s, m.mapping_id, %(variant_id)s,
            %(store_id)s, %(seller_id)s, %(source_url)s, %(url_type)s,
            %(status)s, %(review_status)s, %(title_as_seen)s, %(notes)s,
            %(active)s, %(evidence_verified_at)s,
            COALESCE(%(created_at)s, NOW()), COALESCE(%(updated_at)s, NOW())
        FROM listings m
        WHERE m.offer_key = %(cash_offer_key)s
        ON CONFLICT (task_id) DO UPDATE SET
            mapping_id = EXCLUDED.mapping_id,
            source_url = EXCLUDED.source_url,
            url_type = EXCLUDED.url_type,
            status = CASE
                WHEN installment_tasks.last_checked_at IS NULL THEN EXCLUDED.status
                ELSE installment_tasks.status
            END,
            review_status = CASE
                WHEN installment_tasks.last_checked_at IS NULL THEN EXCLUDED.review_status
                ELSE installment_tasks.review_status
            END,
            title_as_seen = EXCLUDED.title_as_seen,
            notes = CASE
                WHEN installment_tasks.last_checked_at IS NULL THEN EXCLUDED.notes
                ELSE installment_tasks.notes
            END,
            active = EXCLUDED.active,
            evidence_verified_at = EXCLUDED.evidence_verified_at,
            updated_at = NOW()
        """,
        payload,
    )
    conn.commit()
    return len(rows)


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    approved_mapping_ids = operational_mapping_ids()
    approved_offer_keys = {
        row["offer_key"]
        for row in read_rows("store_product_mappings.csv")
        if row.get("mapping_id") in approved_mapping_ids
    }

    apply_all(database_url)
    with psycopg.connect(
        database_url,
        options=f"-c search_path={DATABASE_SEARCH_PATH}",
    ) as conn:
        counts = {
            "products": import_products(conn),
            "variant_aliases": import_variant_aliases(conn),
            "stores": import_stores(conn),
            "connector_configs": import_connectors(conn),
            "mappings": import_mappings(conn, approved_mapping_ids),
            "active_mappings": len(approved_mapping_ids),
            "cash_offers": import_cash_offers(conn, approved_offer_keys),
            "installment_tasks": import_installment_tasks(
                conn, approved_offer_keys
            ),
        }
    print(json.dumps({"ok": True, "imported": counts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
