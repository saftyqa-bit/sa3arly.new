from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from app.db import DATABASE_SEARCH_PATH
from scripts.bootstrap_db import normalize_key, product_family_name, stable_id


@dataclass(frozen=True)
class TableCopy:
    source: str
    target_schema: str
    target: str
    deferred: tuple[str, ...] = ()
    order_by: tuple[str, ...] = ()
    immutable: bool = False


# Parent records and foreign-key owners deliberately precede their dependants.
TABLE_COPIES = (
    TableCopy("currencies", "reference", "currencies"),
    TableCopy("countries", "reference", "countries"),
    TableCopy("audit_log", "governance", "audit_events", immutable=True),
    TableCopy(
        "categories",
        "catalog",
        "categories",
        deferred=("parent_category_id",),
        order_by=("level", "category_id"),
    ),
    TableCopy("brands", "catalog", "brands"),
    TableCopy("product_models", "catalog", "products"),
    TableCopy("product_variants", "catalog", "variants"),
    TableCopy("variant_aliases", "catalog", "variant_aliases"),
    TableCopy("stores", "merchant", "stores"),
    TableCopy("connector_configs", "merchant", "connector_configs"),
    TableCopy("store_rate_limits", "merchant", "store_rate_limits"),
    TableCopy("sellers", "merchant", "sellers"),
    TableCopy("store_product_mappings", "merchant", "listings"),
    TableCopy("current_cash_offers", "pricing", "current_offers"),
    TableCopy(
        "cash_offer_history",
        "pricing",
        "offer_observations",
        order_by=("observed_at", "history_id"),
        immutable=True,
    ),
    TableCopy("installment_discovery_tasks", "ingestion", "installment_tasks"),
    TableCopy("current_installment_plans", "pricing", "current_installment_offers"),
    TableCopy(
        "installment_plan_history",
        "pricing",
        "installment_observations",
        order_by=("observed_at", "history_id"),
        immutable=True,
    ),
    TableCopy("scrape_runs", "operations", "price_runs"),
    TableCopy("scrape_task_runs", "operations", "price_tasks"),
    TableCopy("page_cache", "ingestion", "page_cache"),
    TableCopy("alerts", "governance", "system_alerts"),
    TableCopy("catalog_discovery_runs", "ingestion", "discovery_runs"),
    TableCopy("catalog_discovery_sources", "ingestion", "discovery_sources"),
    TableCopy("catalog_discovery_tasks", "ingestion", "discovery_tasks"),
    TableCopy("catalog_import_runs", "ingestion", "import_runs"),
    TableCopy(
        "catalog_product_entities",
        "ingestion",
        "identity_clusters",
        deferred=("merged_into_entity_id",),
    ),
    TableCopy("catalog_candidates", "ingestion", "discovery_candidates"),
    TableCopy("catalog_import_items", "ingestion", "import_items"),
    TableCopy(
        "catalog_product_observations",
        "ingestion",
        "catalog_observations",
        immutable=True,
    ),
    TableCopy("catalog_task_deliveries", "ingestion", "task_deliveries"),
    TableCopy("data_review_queue", "governance", "review_cases"),
    TableCopy("product_search_aliases", "catalog", "search_aliases"),
    TableCopy("store_quality_metrics", "merchant", "store_quality_metrics"),
    TableCopy("price_alert_rules", "pricing", "alert_rules"),
    TableCopy("price_reports", "pricing", "price_reports"),
    TableCopy("comparison_shares", "pricing", "comparison_shares"),
)

CRITICAL_COPIES = {
    "product_variants": ("catalog", "variants"),
    "stores": ("merchant", "stores"),
    "store_product_mappings": ("merchant", "listings"),
    "current_cash_offers": ("pricing", "current_offers"),
}

TABLE_STAGE_EXIT_CODES = {spec.source: 20 + index for index, spec in enumerate(TABLE_COPIES)}
OWNED_SEQUENCES = (
    ("catalog", "search_aliases", "alias_id", "catalog.search_aliases_alias_id_seq"),
    ("governance", "audit_events", "audit_id", "governance.audit_events_audit_id_seq"),
    ("governance", "system_alerts", "alert_id", "governance.system_alerts_alert_id_seq"),
    ("pricing", "offer_observations", "history_id", "pricing.offer_observations_history_id_seq"),
    (
        "pricing",
        "installment_observations",
        "history_id",
        "pricing.installment_observations_history_id_seq",
    ),
)
NULLABLE_FOREIGN_KEY_REPAIRS = {
    ("merchant", "listings", "seller_id"): (
        "merchant",
        "sellers",
        "seller_id",
    ),
    ("pricing", "current_offers", "seller_id"): (
        "merchant",
        "sellers",
        "seller_id",
    ),
}
_current_stage_exit_code = 10
_current_stage_name = "startup"


def mark_stage(name: str, exit_code: int) -> None:
    global _current_stage_exit_code, _current_stage_name
    _current_stage_name = name
    _current_stage_exit_code = exit_code


def table_exists(conn: psycopg.Connection[Any], schema: str, table: str) -> bool:
    return bool(
        conn.execute(
            "SELECT to_regclass(%s) IS NOT NULL",
            (f"{schema}.{table}",),
        ).fetchone()[0]
    )


def columns(conn: psycopg.Connection[Any], schema: str, table: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
          AND is_generated = 'NEVER'
          AND is_identity = 'NO'
        ORDER BY ordinal_position
        """,
        (schema, table),
    ).fetchall()
    return [str(row[0]) for row in rows]


def primary_key(conn: psycopg.Connection[Any], schema: str, table: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON kcu.constraint_name = tc.constraint_name
         AND kcu.constraint_schema = tc.constraint_schema
        WHERE tc.table_schema = %s
          AND tc.table_name = %s
          AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position
        """,
        (schema, table),
    ).fetchall()
    return [str(row[0]) for row in rows]


def row_count(conn: psycopg.Connection[Any], schema: str, table: str) -> int:
    statement = sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(schema), sql.Identifier(table))
    return int(conn.execute(statement).fetchone()[0])


def source_expression(spec: TableCopy, column_name: str) -> sql.Composable:
    source_column = sql.SQL("source.{}").format(sql.Identifier(column_name))
    repair = NULLABLE_FOREIGN_KEY_REPAIRS.get((spec.target_schema, spec.target, column_name))
    if not repair:
        return source_column
    reference_schema, reference_table, reference_column = repair
    return sql.SQL(
        "CASE WHEN {source_column} IS NULL OR EXISTS ("
        "SELECT 1 FROM {reference_schema}.{reference_table} AS reference "
        "WHERE reference.{reference_column} = {source_column}"
        ") THEN {source_column} ELSE NULL END"
    ).format(
        source_column=source_column,
        reference_schema=sql.Identifier(reference_schema),
        reference_table=sql.Identifier(reference_table),
        reference_column=sql.Identifier(reference_column),
    )


def sync_owned_sequences(conn: psycopg.Connection[Any], schema: str | None = None) -> None:
    for table_schema, table_name, column_name, sequence_name in OWNED_SEQUENCES:
        if schema and table_schema != schema:
            continue
        if schema == "governance":
            mark_stage(f"sequence-maximum:{table_name}", 60)
        maximum = conn.execute(
            sql.SQL("SELECT max({}) FROM {}.{}").format(
                sql.Identifier(column_name),
                sql.Identifier(table_schema),
                sql.Identifier(table_name),
            )
        ).fetchone()[0]
        if maximum is None:
            continue
        if schema == "governance":
            mark_stage(f"sequence-setval:{table_name}", 61)
        conn.execute(
            "SELECT setval(CAST(%s AS regclass), %s, true)",
            (sequence_name, maximum),
        )


def copy_table(conn: psycopg.Connection[Any], spec: TableCopy) -> dict[str, int]:
    if not table_exists(conn, "public", spec.source):
        raise RuntimeError(f"Required legacy table is missing: public.{spec.source}")
    if not table_exists(conn, spec.target_schema, spec.target):
        raise RuntimeError(f"Required Core V2 table is missing: {spec.target_schema}.{spec.target}")

    source_columns = set(columns(conn, "public", spec.source))
    target_columns = columns(conn, spec.target_schema, spec.target)
    common = [name for name in target_columns if name in source_columns and name not in spec.deferred]
    if not common:
        raise RuntimeError(f"No compatible columns for public.{spec.source}")

    target_identifier = sql.SQL("{}.{}").format(
        sql.Identifier(spec.target_schema), sql.Identifier(spec.target)
    )
    source_identifier = sql.SQL("{}.{}").format(sql.Identifier("public"), sql.Identifier(spec.source))
    identifiers = sql.SQL(", ").join(map(sql.Identifier, common))
    source_expressions = sql.SQL(", ").join(source_expression(spec, name) for name in common)
    order = sql.SQL("")
    valid_order = [name for name in spec.order_by if name in source_columns]
    if valid_order:
        order = sql.SQL(" ORDER BY ") + sql.SQL(", ").join(
            sql.SQL("source.{}").format(sql.Identifier(name)) for name in valid_order
        )

    key_columns = primary_key(conn, spec.target_schema, spec.target)
    mutable = [name for name in common if name not in key_columns]
    if spec.immutable or not key_columns or not mutable:
        conflict = sql.SQL(" ON CONFLICT DO NOTHING")
    else:
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(name), sql.Identifier(name)) for name in mutable
        )
        conflict = (
            sql.SQL(" ON CONFLICT ({}) DO UPDATE SET ").format(
                sql.SQL(", ").join(map(sql.Identifier, key_columns))
            )
            + assignments
        )

    statement = (
        sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {} AS source").format(
            target_identifier,
            identifiers,
            source_expressions,
            source_identifier,
        )
        + order
        + conflict
    )
    conn.execute(statement)
    return {
        "source": row_count(conn, "public", spec.source),
        "target": row_count(conn, spec.target_schema, spec.target),
    }


def restore_deferred_links(conn: psycopg.Connection[Any]) -> None:
    conn.execute(
        """
        UPDATE catalog.categories AS target
        SET parent_category_id = source.parent_category_id
        FROM public.categories AS source
        WHERE source.category_id = target.category_id
          AND target.parent_category_id IS DISTINCT FROM source.parent_category_id
        """
    )
    conn.execute(
        """
        UPDATE ingestion.identity_clusters AS target
        SET merged_into_entity_id = source.merged_into_entity_id
        FROM public.catalog_product_entities AS source
        WHERE source.entity_id = target.entity_id
          AND target.merged_into_entity_id IS DISTINCT FROM source.merged_into_entity_id
        """
    )


def build_product_hierarchy(conn: psycopg.Connection[Any]) -> dict[str, int]:
    product_rows = conn.execute(
        """
        SELECT p.product_id, p.category_id, p.brand_id, p.canonical_name, p.model,
               representative.product_type
        FROM catalog.products AS p
        LEFT JOIN LATERAL (
            SELECT v.product_type
            FROM catalog.variants AS v
            WHERE v.product_id = p.product_id
            ORDER BY v.updated_at DESC, v.variant_id
            LIMIT 1
        ) AS representative ON TRUE
        """
    ).fetchall()
    families: dict[str, tuple[Any, ...]] = {}
    product_families: list[tuple[str, str]] = []
    for product_id, category_id, brand_id, canonical_name, model, product_type in product_rows:
        model_name = str(model or canonical_name or product_id).strip()
        family_name = product_family_name(model_name, product_type) or model_name
        family_id = stable_id("FAM", category_id, brand_id, family_name, length=18)
        families[family_id] = (
            family_id,
            category_id,
            brand_id,
            family_name,
            normalize_key(family_name),
        )
        product_families.append((family_id, str(product_id)))

    with conn.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO catalog.product_families (
                family_id, category_id, brand_id, canonical_name, normalized_name
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (family_id) DO UPDATE SET
                canonical_name = EXCLUDED.canonical_name,
                normalized_name = EXCLUDED.normalized_name,
                updated_at = NOW()
            """,
            families.values(),
        )
        cursor.executemany(
            """
            UPDATE catalog.products
            SET family_id = %s, updated_at = GREATEST(updated_at, NOW())
            WHERE product_id = %s
            """,
            product_families,
        )
    return {"families": len(families), "products": len(product_rows)}


def build_variant_details(conn: psycopg.Connection[Any]) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT variant_id, category_id, ram_gb, storage_gb, color,
               gtin, manufacturer_sku
        FROM catalog.variants
        """
    ).fetchall()
    definitions: dict[str, tuple[Any, ...]] = {}
    values: list[tuple[Any, ...]] = []
    identifiers: list[tuple[Any, ...]] = []
    for variant_id, category_id, ram_gb, storage_gb, color, gtin, sku in rows:
        if category_id:
            for code, name_ar, value_type, value, unit in (
                ("ram_gb", "الذاكرة", "number", ram_gb, "GB"),
                ("storage_gb", "السعة التخزينية", "number", storage_gb, "GB"),
                ("color", "اللون", "text", color, None),
            ):
                if value is None or str(value).strip() == "":
                    continue
                attribute_id = stable_id("ATR", category_id, code, length=18)
                definitions[attribute_id] = (
                    attribute_id,
                    category_id,
                    code,
                    name_ar,
                    code,
                    value_type,
                    unit,
                )
                values.append(
                    (
                        variant_id,
                        attribute_id,
                        str(value) if value_type == "text" else None,
                        value if value_type == "number" else None,
                        normalize_key(value) if value_type == "text" else None,
                        unit,
                    )
                )
        for identifier_type, identifier_value in (
            ("gtin", gtin),
            ("manufacturer_sku", sku),
        ):
            if identifier_value and str(identifier_value).strip():
                identifiers.append(
                    (
                        variant_id,
                        identifier_type,
                        str(identifier_value).strip(),
                        normalize_key(identifier_value),
                    )
                )

    with conn.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO catalog.attribute_definitions (
                attribute_id, category_id, code, name_ar, name_en,
                value_type, default_unit
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (attribute_id) DO UPDATE SET
                name_ar = EXCLUDED.name_ar,
                default_unit = EXCLUDED.default_unit,
                updated_at = NOW()
            """,
            definitions.values(),
        )
        cursor.executemany(
            """
            INSERT INTO catalog.variant_attribute_values (
                variant_id, attribute_id, value_text, value_number,
                normalized_text, unit
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (variant_id, attribute_id, value_index) DO UPDATE SET
                value_text = EXCLUDED.value_text,
                value_number = EXCLUDED.value_number,
                normalized_text = EXCLUDED.normalized_text,
                unit = EXCLUDED.unit,
                updated_at = NOW()
            """,
            values,
        )
        cursor.executemany(
            """
            INSERT INTO catalog.variant_identifiers (
                variant_id, identifier_type, identifier_value, normalized_value
            ) VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            identifiers,
        )
    return {
        "attribute_definitions": len(definitions),
        "attribute_values": len(values),
        "identifiers_seen": len(identifiers),
    }


def validate_copy(
    conn: psycopg.Connection[Any], copy_counts: dict[str, dict[str, int]]
) -> dict[str, int | str | None]:
    failures: list[str] = []
    for source, (target_schema, target) in CRITICAL_COPIES.items():
        counts = copy_counts[source]
        if counts["source"] != counts["target"]:
            failures.append(f"{source}->{target_schema}.{target}: {counts['source']} != {counts['target']}")

    facts = conn.execute(
        """
        SELECT
            (SELECT count(*) FROM pricing.current_offers
             WHERE cash_price IS NOT NULL) AS priced_offers,
            (SELECT count(*) FROM pricing.public_offer_table
             WHERE visible) AS visible_offers,
            (SELECT count(*) FROM merchant.listing_urls
             WHERE is_primary) AS primary_urls,
            (SELECT max(last_success_at) FROM pricing.current_offers)
                AS latest_cash_update
        """
    ).fetchone()
    validation: dict[str, int | str | None] = {
        "priced_offers": int(facts[0]),
        "visible_offers": int(facts[1]),
        "primary_urls": int(facts[2]),
        "latest_cash_update": facts[3].isoformat() if facts[3] else None,
    }
    if validation["priced_offers"] == 0:
        failures.append("Core V2 has no priced offers")
    if validation["visible_offers"] == 0:
        failures.append("Core V2 has no visible offers")
    if validation["primary_urls"] == 0:
        failures.append("Core V2 has no primary listing URLs")
    if failures:
        raise RuntimeError("Core V2 validation failed: " + "; ".join(failures))
    return validation


def copy_all(database_url: str) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    mark_stage("database-connect", 11)
    with psycopg.connect(
        database_url,
        options=f"-c search_path={DATABASE_SEARCH_PATH}",
    ) as conn:
        mark_stage("transaction-setup", 12)
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        conn.execute("SELECT pg_advisory_xact_lock(hashtext('sa3arly_core_v2_cutover'))")
        mark_stage("completion-check", 13)
        completed = conn.execute("SELECT count(*) FROM governance.cutover_runs").fetchone()[0]
        if completed and os.environ.get("CORE_V2_RECOPY") != "1":
            return {"ok": True, "skipped": "already_completed"}

        copy_counts: dict[str, dict[str, int]] = {}
        for spec in TABLE_COPIES:
            mark_stage(f"copy:{spec.source}", TABLE_STAGE_EXIT_CODES[spec.source])
            copy_counts[spec.source] = copy_table(conn, spec)
            if spec.target_schema == "governance" and spec.target == "audit_events":
                mark_stage("sync-audit-sequences", 69)
                sync_owned_sequences(conn, "governance")

        mark_stage("restore-deferred-links", 70)
        restore_deferred_links(conn)
        mark_stage("build-product-hierarchy", 71)
        hierarchy = build_product_hierarchy(conn)
        mark_stage("build-variant-details", 72)
        variant_details = build_variant_details(conn)
        mark_stage("sync-sequences", 73)
        sync_owned_sequences(conn)
        mark_stage("validate-copy", 74)
        validation = validate_copy(conn, copy_counts)

        source_counts = {source: counts["source"] for source, counts in copy_counts.items()}
        target_counts = {source: counts["target"] for source, counts in copy_counts.items()}
        target_counts.update(hierarchy)
        target_counts.update(variant_details)
        target_counts.update(validation)
        mark_stage("record-cutover", 75)
        conn.execute(
            """
            INSERT INTO governance.cutover_runs (
                status, source_counts, target_counts, started_at
            ) VALUES ('verified', %s, %s, %s)
            """,
            (Jsonb(source_counts), Jsonb(target_counts), started_at),
        )
        return {
            "ok": True,
            "source_counts": source_counts,
            "target_counts": target_counts,
        }


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")
    try:
        result = copy_all(database_url)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "stage": _current_stage_name,
                    "exit_code": _current_stage_exit_code,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(_current_stage_exit_code) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print("CORE_V2_DATA_COPY=PASS")


if __name__ == "__main__":
    main()
