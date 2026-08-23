from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import psycopg
from google.cloud import firestore
from psycopg.types.json import Jsonb

MONEY_SCALE = Decimal("100")
CASH_MONEY_FIELDS = (
    "cash_price",
    "old_price",
    "discount_amount",
    "shipping_cost",
    "total_price",
)
PLAN_MONEY_FIELDS = (
    "periodic_payment",
    "first_payment",
    "down_payment",
    "admin_fees",
    "processing_fees",
    "insurance_fees",
    "other_fees",
    "total_published",
    "total_calculated",
    "cash_price_at_observation",
    "financing_cost",
    "minimum_purchase",
    "maximum_financing",
)
ARCHIVE_COLLECTIONS = (
    "scrape_runs",
    "scrape_task_runs",
    "system",
)


@dataclass(frozen=True)
class SourceDocument:
    collection: str
    document_id: str
    payload: dict[str, Any]


def jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def checksum(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        jsonable(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def minor_to_amount(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return (Decimal(str(value)) / MONEY_SCALE).quantize(Decimal("0.01"))


def as_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def valid_uuid(value: Any) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def raw_payload_with_legacy_run(row: dict[str, Any]) -> dict[str, Any]:
    payload = jsonable(row.get("raw_payload") or {})
    if not isinstance(payload, dict):
        payload = {"value": payload}
    legacy_run_id = row.get("last_run_id")
    if legacy_run_id and valid_uuid(legacy_run_id) is None:
        payload["_firestore_last_run_id"] = str(legacy_run_id)
    return payload


def collection_name(prefix: str, name: str) -> str:
    return f"{prefix}{name}"


def read_collection(
    client: firestore.Client, prefix: str, name: str
) -> list[SourceDocument]:
    return [
        SourceDocument(name, snapshot.id, snapshot.to_dict() or {})
        for snapshot in client.collection(collection_name(prefix, name)).stream()
    ]


def fetch_source(
    client: firestore.Client, prefix: str
) -> dict[str, list[SourceDocument]]:
    names = (
        "product_variants",
        "stores",
        "mappings",
        "cash_offers",
        "installment_plans",
        "cash_offer_history",
        "installment_plan_history",
        "installment_discovery",
        *ARCHIVE_COLLECTIONS,
    )
    return {name: read_collection(client, prefix, name) for name in names}


def create_migration_run(conn: psycopg.Connection) -> uuid.UUID:
    row = conn.execute(
        """
        INSERT INTO migration_runs (source_backend, target_backend)
        VALUES ('firestore', 'postgres')
        RETURNING migration_run_id
        """
    ).fetchone()
    conn.commit()
    return row[0]


def archive_documents(
    conn: psycopg.Connection, documents: Iterable[SourceDocument]
) -> int:
    count = 0
    for document in documents:
        payload = jsonable(document.payload)
        updated_at = as_datetime(
            document.payload.get("updated_at")
            or document.payload.get("completed_at")
            or document.payload.get("started_at")
        )
        conn.execute(
            """
            INSERT INTO legacy_firestore_records (
                collection_name, document_id, payload, source_updated_at
            )
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (collection_name, document_id) DO UPDATE SET
                payload = EXCLUDED.payload,
                source_updated_at = EXCLUDED.source_updated_at,
                archived_at = NOW()
            """,
            (
                document.collection,
                document.document_id,
                Jsonb(payload),
                updated_at,
            ),
        )
        count += 1
    return count


def migrate_mapping_runtime(
    conn: psycopg.Connection, documents: list[SourceDocument]
) -> int:
    count = 0
    for document in documents:
        row = document.payload
        result = conn.execute(
            """
            UPDATE store_product_mappings
            SET direct_product_url = %s,
                title_as_seen = COALESCE(%s, title_as_seen),
                match_method = COALESCE(%s, match_method),
                match_confidence = COALESCE(%s, match_confidence),
                metadata = metadata || %s,
                last_discovered_at = %s,
                review_status = COALESCE(%s, review_status),
                active = %s,
                updated_at = GREATEST(updated_at, COALESCE(%s, updated_at))
            WHERE mapping_id = %s
            """,
            (
                row.get("direct_product_url"),
                row.get("title_as_seen"),
                row.get("match_method"),
                row.get("match_confidence"),
                Jsonb(jsonable(row.get("metadata") or {})),
                as_datetime(row.get("last_discovered_at")),
                row.get("review_status"),
                bool(row.get("active", False)),
                as_datetime(row.get("updated_at")),
                row.get("mapping_id") or document.document_id,
            ),
        )
        count += result.rowcount
    return count


def migrate_cash_offers(
    conn: psycopg.Connection, documents: list[SourceDocument]
) -> int:
    count = 0
    for document in documents:
        row = document.payload
        values = {field: minor_to_amount(row.get(f"{field}_minor")) for field in CASH_MONEY_FIELDS}
        result = conn.execute(
            """
            UPDATE current_cash_offers
            SET seller_id = %s,
                seller_name = %s,
                currency = COALESCE(%s, currency),
                cash_price = %s,
                old_price = %s,
                discount_amount = %s,
                discount_percent = %s,
                shipping_cost = %s,
                total_price = %s,
                free_shipping = %s,
                availability = %s,
                available_quantity = %s,
                delivery_region = COALESCE(%s, delivery_region),
                delivery_text = %s,
                min_delivery_days = %s,
                max_delivery_days = %s,
                warranty_type = %s,
                warranty_provider = %s,
                warranty_months = %s,
                source_method = %s,
                source_url = %s,
                last_checked_at = %s,
                last_success_at = %s,
                freshness_status = COALESCE(%s, freshness_status),
                extraction_status = COALESCE(%s, extraction_status),
                consecutive_failures = COALESCE(%s, consecutive_failures),
                connector_version = %s,
                last_run_id = %s,
                review_status = COALESCE(%s, review_status),
                review_notes = %s,
                raw_payload = %s,
                updated_at = GREATEST(updated_at, COALESCE(%s, updated_at))
            WHERE offer_key = %s
            """,
            (
                row.get("seller_id"),
                row.get("seller_name"),
                row.get("currency"),
                values["cash_price"],
                values["old_price"],
                values["discount_amount"],
                row.get("discount_percent"),
                values["shipping_cost"],
                values["total_price"],
                row.get("free_shipping"),
                row.get("availability"),
                row.get("available_quantity"),
                row.get("delivery_region"),
                row.get("delivery_text"),
                row.get("min_delivery_days"),
                row.get("max_delivery_days"),
                row.get("warranty_type"),
                row.get("warranty_provider"),
                row.get("warranty_months"),
                row.get("source_method"),
                row.get("source_url"),
                as_datetime(row.get("last_checked_at")),
                as_datetime(row.get("last_success_at")),
                row.get("freshness_status"),
                row.get("extraction_status"),
                row.get("consecutive_failures"),
                row.get("connector_version"),
                valid_uuid(row.get("last_run_id")),
                row.get("review_status"),
                row.get("review_notes"),
                Jsonb(raw_payload_with_legacy_run(row)),
                as_datetime(row.get("updated_at")),
                row.get("offer_key") or document.document_id,
            ),
        )
        count += result.rowcount
    return count


def migrate_installment_plans(
    conn: psycopg.Connection, documents: list[SourceDocument]
) -> int:
    count = 0
    for document in documents:
        row = document.payload
        amounts = {
            field: minor_to_amount(row.get(f"{field}_minor"))
            for field in PLAN_MONEY_FIELDS
        }
        conn.execute(
            """
            INSERT INTO current_installment_plans (
                plan_id, plan_key, cash_offer_key, variant_id, store_id,
                seller_id, seller_name, provider_id, provider_name, provider_type,
                bank_or_card, plan_name, months, payment_frequency,
                periodic_payment, first_payment, down_payment,
                down_payment_percent, admin_fees, processing_fees,
                insurance_fees, other_fees, total_published, total_calculated,
                cash_price_at_observation, financing_cost,
                financing_markup_percent, apr, interest_type, interest_free,
                grace_months, minimum_purchase, maximum_financing, eligibility,
                required_card, customer_type, new_customers_only, geography,
                starts_at, ends_at, promo_code, terms_url, source_url,
                starting_from_only, completeness, last_checked_at,
                last_success_at, freshness_status, extraction_status,
                consecutive_failures, connector_version, last_run_id, active,
                review_status, review_notes, raw_payload, created_at, updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, COALESCE(%s, NOW()), COALESCE(%s, NOW())
            )
            ON CONFLICT (plan_key) DO UPDATE SET
                provider_id = EXCLUDED.provider_id,
                provider_name = EXCLUDED.provider_name,
                provider_type = EXCLUDED.provider_type,
                bank_or_card = EXCLUDED.bank_or_card,
                plan_name = EXCLUDED.plan_name,
                months = EXCLUDED.months,
                payment_frequency = EXCLUDED.payment_frequency,
                periodic_payment = EXCLUDED.periodic_payment,
                first_payment = EXCLUDED.first_payment,
                down_payment = EXCLUDED.down_payment,
                down_payment_percent = EXCLUDED.down_payment_percent,
                admin_fees = EXCLUDED.admin_fees,
                processing_fees = EXCLUDED.processing_fees,
                insurance_fees = EXCLUDED.insurance_fees,
                other_fees = EXCLUDED.other_fees,
                total_published = EXCLUDED.total_published,
                total_calculated = EXCLUDED.total_calculated,
                cash_price_at_observation = EXCLUDED.cash_price_at_observation,
                financing_cost = EXCLUDED.financing_cost,
                financing_markup_percent = EXCLUDED.financing_markup_percent,
                apr = EXCLUDED.apr,
                interest_type = EXCLUDED.interest_type,
                interest_free = EXCLUDED.interest_free,
                grace_months = EXCLUDED.grace_months,
                minimum_purchase = EXCLUDED.minimum_purchase,
                maximum_financing = EXCLUDED.maximum_financing,
                eligibility = EXCLUDED.eligibility,
                required_card = EXCLUDED.required_card,
                customer_type = EXCLUDED.customer_type,
                new_customers_only = EXCLUDED.new_customers_only,
                geography = EXCLUDED.geography,
                starts_at = EXCLUDED.starts_at,
                ends_at = EXCLUDED.ends_at,
                promo_code = EXCLUDED.promo_code,
                terms_url = EXCLUDED.terms_url,
                source_url = EXCLUDED.source_url,
                starting_from_only = EXCLUDED.starting_from_only,
                completeness = EXCLUDED.completeness,
                last_checked_at = EXCLUDED.last_checked_at,
                last_success_at = EXCLUDED.last_success_at,
                freshness_status = EXCLUDED.freshness_status,
                extraction_status = EXCLUDED.extraction_status,
                consecutive_failures = EXCLUDED.consecutive_failures,
                connector_version = EXCLUDED.connector_version,
                active = EXCLUDED.active,
                review_status = EXCLUDED.review_status,
                review_notes = EXCLUDED.review_notes,
                raw_payload = EXCLUDED.raw_payload,
                updated_at = GREATEST(current_installment_plans.updated_at, EXCLUDED.updated_at)
            """,
            (
                row.get("plan_id") or document.document_id,
                row.get("plan_key") or document.document_id,
                row.get("cash_offer_key"),
                row.get("variant_id"),
                row.get("store_id"),
                row.get("seller_id"),
                row.get("seller_name"),
                row.get("provider_id"),
                row.get("provider_name"),
                row.get("provider_type"),
                row.get("bank_or_card"),
                row.get("plan_name"),
                row.get("months"),
                row.get("payment_frequency") or "monthly",
                amounts["periodic_payment"],
                amounts["first_payment"],
                amounts["down_payment"],
                row.get("down_payment_percent"),
                amounts["admin_fees"],
                amounts["processing_fees"],
                amounts["insurance_fees"],
                amounts["other_fees"],
                amounts["total_published"],
                amounts["total_calculated"],
                amounts["cash_price_at_observation"],
                amounts["financing_cost"],
                row.get("financing_markup_percent"),
                row.get("apr"),
                row.get("interest_type"),
                row.get("interest_free"),
                row.get("grace_months"),
                amounts["minimum_purchase"],
                amounts["maximum_financing"],
                row.get("eligibility"),
                row.get("required_card"),
                row.get("customer_type"),
                row.get("new_customers_only"),
                row.get("geography"),
                as_datetime(row.get("starts_at")),
                as_datetime(row.get("ends_at")),
                row.get("promo_code"),
                row.get("terms_url"),
                row.get("source_url"),
                bool(row.get("starting_from_only", False)),
                row.get("completeness"),
                as_datetime(row.get("last_checked_at")),
                as_datetime(row.get("last_success_at")),
                row.get("freshness_status") or "unseen",
                row.get("extraction_status") or "pending",
                int(row.get("consecutive_failures") or 0),
                row.get("connector_version"),
                valid_uuid(row.get("last_run_id")),
                bool(row.get("active", True)),
                row.get("review_status"),
                row.get("review_notes"),
                Jsonb(raw_payload_with_legacy_run(row)),
                as_datetime(row.get("created_at")),
                as_datetime(row.get("updated_at")),
            ),
        )
        count += 1
    return count


def migrate_discovery(
    conn: psycopg.Connection, documents: list[SourceDocument]
) -> int:
    count = 0
    for document in documents:
        row = document.payload
        result = conn.execute(
            """
            UPDATE installment_discovery_tasks
            SET source_url = COALESCE(%s, source_url),
                status = COALESCE(%s, status),
                review_status = COALESCE(%s, review_status),
                notes = %s,
                last_checked_at = %s,
                last_success_at = %s,
                consecutive_failures = COALESCE(%s, consecutive_failures),
                updated_at = GREATEST(updated_at, COALESCE(%s, updated_at))
            WHERE cash_offer_key = %s
            """,
            (
                row.get("source_url"),
                row.get("status"),
                row.get("review_status"),
                row.get("notes"),
                as_datetime(row.get("last_checked_at")),
                as_datetime(row.get("last_success_at")),
                row.get("consecutive_failures"),
                as_datetime(row.get("updated_at")),
                row.get("cash_offer_key") or document.document_id,
            ),
        )
        count += result.rowcount
    return count


def already_migrated(
    conn: psycopg.Connection, document: SourceDocument, target_table: str
) -> bool:
    return bool(
        conn.execute(
            """
            SELECT 1
            FROM migration_source_records
            WHERE source_collection = %s
              AND source_document_id = %s
              AND target_table = %s
            """,
            (document.collection, document.document_id, target_table),
        ).fetchone()
    )


def record_migration(
    conn: psycopg.Connection,
    document: SourceDocument,
    target_table: str,
    target_key: str,
) -> None:
    conn.execute(
        """
        INSERT INTO migration_source_records (
            source_collection, source_document_id, target_table, target_key, checksum
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (source_collection, source_document_id, target_table) DO NOTHING
        """,
        (
            document.collection,
            document.document_id,
            target_table,
            target_key,
            checksum(document.payload),
        ),
    )


def migrate_cash_history(
    conn: psycopg.Connection, documents: list[SourceDocument]
) -> int:
    count = 0
    for document in documents:
        if already_migrated(conn, document, "cash_offer_history"):
            continue
        row = document.payload
        snapshot = jsonable(row.get("snapshot") or {})
        snapshot["_firestore_run_id"] = row.get("run_id")
        result = conn.execute(
            """
            INSERT INTO cash_offer_history (
                offer_key, variant_id, store_id, seller_id, observed_at,
                run_id, change_type, cash_price, old_price, shipping_cost,
                total_price, availability, warranty_type, warranty_provider,
                warranty_months, snapshot
            )
            VALUES (%s, %s, %s, %s, COALESCE(%s, NOW()), NULL, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s)
            RETURNING history_id
            """,
            (
                row.get("offer_key"),
                row.get("variant_id"),
                row.get("store_id"),
                row.get("seller_id"),
                as_datetime(row.get("observed_at")),
                row.get("change_type") or "legacy_firestore",
                minor_to_amount(row.get("cash_price_minor")),
                minor_to_amount(row.get("old_price_minor")),
                minor_to_amount(row.get("shipping_cost_minor")),
                minor_to_amount(row.get("total_price_minor")),
                row.get("availability"),
                row.get("warranty_type"),
                row.get("warranty_provider"),
                row.get("warranty_months"),
                Jsonb(snapshot),
            ),
        ).fetchone()
        record_migration(conn, document, "cash_offer_history", str(result[0]))
        count += 1
    return count


def migrate_installment_history(
    conn: psycopg.Connection, documents: list[SourceDocument]
) -> int:
    count = 0
    for document in documents:
        if already_migrated(conn, document, "installment_plan_history"):
            continue
        row = document.payload
        snapshot = jsonable(row.get("snapshot") or {})
        snapshot["_firestore_run_id"] = row.get("run_id")
        result = conn.execute(
            """
            INSERT INTO installment_plan_history (
                plan_key, cash_offer_key, variant_id, store_id, observed_at,
                run_id, change_type, snapshot
            )
            VALUES (%s, %s, %s, %s, COALESCE(%s, NOW()), NULL, %s, %s)
            RETURNING history_id
            """,
            (
                row.get("plan_key"),
                row.get("cash_offer_key"),
                row.get("variant_id"),
                row.get("store_id"),
                as_datetime(row.get("observed_at")),
                row.get("change_type") or "legacy_firestore",
                Jsonb(snapshot),
            ),
        ).fetchone()
        record_migration(conn, document, "installment_plan_history", str(result[0]))
        count += 1
    return count


def synchronize_operational_flags(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        UPDATE current_cash_offers o
        SET active = m.active,
            updated_at = NOW()
        FROM store_product_mappings m
        WHERE m.mapping_id = o.mapping_id
          AND o.active IS DISTINCT FROM m.active
        """
    )
    conn.execute(
        """
        UPDATE installment_discovery_tasks d
        SET active = m.active,
            updated_at = NOW()
        FROM store_product_mappings m
        WHERE m.mapping_id = d.mapping_id
          AND d.active IS DISTINCT FROM m.active
        """
    )
    conn.execute(
        """
        UPDATE current_installment_plans i
        SET active = FALSE,
            updated_at = NOW()
        FROM current_cash_offers o
        WHERE o.offer_key = i.cash_offer_key
          AND o.active = FALSE
          AND i.active = TRUE
        """
    )


def source_counts(source: dict[str, list[SourceDocument]]) -> dict[str, int]:
    return {
        "products": len(source["product_variants"]),
        "stores": len(source["stores"]),
        "mappings": len(source["mappings"]),
        "active_mappings": sum(
            bool(item.payload.get("active")) for item in source["mappings"]
        ),
        "cash_offers": len(source["cash_offers"]),
        "priced_cash_offers": sum(
            item.payload.get("cash_price_minor") is not None
            for item in source["cash_offers"]
        ),
        "installment_plans": len(source["installment_plans"]),
        "installment_discovery": len(source["installment_discovery"]),
        "cash_history": len(source["cash_offer_history"]),
        "installment_history": len(source["installment_plan_history"]),
        "archived_run_records": sum(len(source[name]) for name in ARCHIVE_COLLECTIONS),
    }


def target_counts(conn: psycopg.Connection) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM product_variants) AS products,
            (SELECT COUNT(*) FROM stores) AS stores,
            (SELECT COUNT(*) FROM store_product_mappings) AS mappings,
            (SELECT COUNT(*) FROM store_product_mappings WHERE active) AS active_mappings,
            (SELECT COUNT(*) FROM current_cash_offers) AS cash_offers,
            (SELECT COUNT(*) FROM current_cash_offers WHERE cash_price IS NOT NULL) AS priced_cash_offers,
            (SELECT COUNT(*) FROM current_installment_plans) AS installment_plans,
            (SELECT COUNT(*) FROM cash_offer_history) AS cash_history,
            (SELECT COUNT(*) FROM installment_plan_history) AS installment_history,
            (SELECT COUNT(*) FROM legacy_firestore_records) AS archived_run_records,
            (
                SELECT COUNT(*)
                FROM store_product_mappings m
                LEFT JOIN product_variants p ON p.variant_id = m.variant_id
                LEFT JOIN stores s ON s.store_id = m.store_id
                WHERE p.variant_id IS NULL OR s.store_id IS NULL
            ) AS orphan_mappings,
            (
                SELECT COUNT(*)
                FROM current_cash_offers o
                LEFT JOIN store_product_mappings m ON m.mapping_id = o.mapping_id
                WHERE m.mapping_id IS NULL
            ) AS orphan_cash_offers,
            (
                SELECT COUNT(*)
                FROM current_installment_plans i
                LEFT JOIN product_variants p ON p.variant_id = i.variant_id
                LEFT JOIN stores s ON s.store_id = i.store_id
                WHERE p.variant_id IS NULL OR s.store_id IS NULL
            ) AS orphan_installment_plans
        """
    ).fetchone()
    names = (
        "products",
        "stores",
        "mappings",
        "active_mappings",
        "cash_offers",
        "priced_cash_offers",
        "installment_plans",
        "cash_history",
        "installment_history",
        "archived_run_records",
        "orphan_mappings",
        "orphan_cash_offers",
        "orphan_installment_plans",
    )
    return {name: int(value or 0) for name, value in zip(names, row, strict=True)}


def verify_counts(
    source: dict[str, int],
    target: dict[str, int],
    migrated: dict[str, int],
) -> dict[str, Any]:
    checks = {
        "products_preserved": target["products"] >= source["products"],
        "stores_preserved": target["stores"] >= source["stores"],
        "active_mapping_set_preserved": target["active_mappings"] == source["active_mappings"],
        "cash_rows_preserved": target["cash_offers"] >= source["cash_offers"],
        "priced_cash_preserved": target["priced_cash_offers"] >= source["priced_cash_offers"],
        "installment_plans_preserved": target["installment_plans"] >= source["installment_plans"],
        "mapping_runtime_matched": migrated["mapping_runtime"] == source["mappings"],
        "cash_runtime_matched": migrated["cash_offers"] == source["cash_offers"],
        "installment_runtime_matched": migrated["installment_plans"] == source["installment_plans"],
        "discovery_runtime_matched": migrated["installment_discovery"] == source["installment_discovery"],
        "legacy_archive_matched": migrated["legacy_archive"] == source["archived_run_records"],
        "cash_history_preserved": target["cash_history"] >= source["cash_history"],
        "installment_history_preserved": target["installment_history"] >= source["installment_history"],
        "legacy_runs_archived": target["archived_run_records"] >= source["archived_run_records"],
        "no_orphan_mappings": target["orphan_mappings"] == 0,
        "no_orphan_cash_offers": target["orphan_cash_offers"] == 0,
        "no_orphan_installment_plans": target["orphan_installment_plans"] == 0,
    }
    return {"ok": all(checks.values()), "checks": checks}


def finish_migration_run(
    conn: psycopg.Connection,
    migration_run_id: uuid.UUID,
    source: dict[str, int],
    target: dict[str, int],
    verification: dict[str, Any],
    *,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE migration_runs
        SET completed_at = NOW(),
            status = %s,
            source_counts = %s,
            target_counts = %s,
            verification = %s,
            notes = %s
        WHERE migration_run_id = %s
        """,
        (
            "completed" if verification.get("ok") else "failed_verification",
            Jsonb(source),
            Jsonb(target),
            Jsonb(verification),
            error,
            migration_run_id,
        ),
    )
    conn.commit()


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    project_id = os.environ.get("FIRESTORE_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    database = os.environ.get("FIRESTORE_DATABASE", "(default)")
    prefix = os.environ.get("FIRESTORE_COLLECTION_PREFIX", "")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    if not project_id:
        raise SystemExit("FIRESTORE_PROJECT_ID or GOOGLE_CLOUD_PROJECT is required")

    firestore_client = firestore.Client(project=project_id, database=database)
    source = fetch_source(firestore_client, prefix)
    source_summary = source_counts(source)

    with psycopg.connect(database_url) as conn:
        migration_run_id = create_migration_run(conn)
        try:
            legacy_archive = archive_documents(
                conn,
                (
                    document
                    for name in ARCHIVE_COLLECTIONS
                    for document in source[name]
                ),
            )
            migrated = {
                "legacy_archive": legacy_archive,
                "mapping_runtime": migrate_mapping_runtime(conn, source["mappings"]),
                "cash_offers": migrate_cash_offers(conn, source["cash_offers"]),
                "installment_plans": migrate_installment_plans(
                    conn, source["installment_plans"]
                ),
                "installment_discovery": migrate_discovery(
                    conn, source["installment_discovery"]
                ),
                "cash_history": migrate_cash_history(
                    conn, source["cash_offer_history"]
                ),
                "installment_history": migrate_installment_history(
                    conn, source["installment_plan_history"]
                ),
            }
            synchronize_operational_flags(conn)
            conn.commit()
            target_summary = target_counts(conn)
            verification = verify_counts(source_summary, target_summary, migrated)
            verification["migrated"] = migrated
            finish_migration_run(
                conn,
                migration_run_id,
                source_summary,
                target_summary,
                verification,
            )
        except Exception as exc:
            conn.rollback()
            finish_migration_run(
                conn,
                migration_run_id,
                source_summary,
                {},
                {"ok": False, "checks": {}},
                error=str(exc),
            )
            raise

    output = {
        "ok": verification["ok"],
        "migration_run_id": str(migration_run_id),
        "source": source_summary,
        "target": target_summary,
        "verification": verification,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if not verification["ok"]:
        raise SystemExit("Migration verification failed; keep the queue and scheduler paused")


if __name__ == "__main__":
    main()
