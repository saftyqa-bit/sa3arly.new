from __future__ import annotations

from typing import Any

from app.db import connection
from app.settings import get_settings


def list_public_stores(*, query: str | None = None, limit: int = 500) -> dict[str, Any]:
    """Return the truthful store registry with connection and price coverage."""
    limit = max(1, min(int(limit), 500))
    query = (query or "").strip()[:120] or None
    if get_settings().persistence_backend == "firestore":
        return {"items": [], "total": 0, "limit": limit}

    with connection() as conn:
        rows = conn.execute(
            """
            WITH mapping_stats AS (
                SELECT store_id, COUNT(*) AS mapping_count,
                       COUNT(DISTINCT variant_id) AS mapped_product_count
                FROM listings
                WHERE active
                GROUP BY store_id
            ),
            cash_stats AS (
                SELECT store_id, COUNT(*) AS cash_offer_count,
                       COUNT(*) FILTER (WHERE anomaly_status = 'clear') AS verified_cash_offer_count,
                       COUNT(*) FILTER (WHERE anomaly_status = 'review') AS review_cash_offer_count,
                       COUNT(DISTINCT variant_id) AS priced_product_count,
                       COUNT(DISTINCT variant_id) FILTER (
                           WHERE anomaly_status = 'clear'
                       ) AS verified_priced_product_count,
                       COUNT(DISTINCT variant_id) FILTER (
                           WHERE anomaly_status = 'review'
                       ) AS review_priced_product_count,
                       MAX(last_success_at) AS latest_cash_update
                FROM current_offers
                WHERE active
                  AND anomaly_status <> 'blocked'
                  AND currency = 'EGP'
                  AND cash_price >= 10
                GROUP BY store_id
            ),
            installment_stats AS (
                SELECT store_id, COUNT(*) AS installment_plan_count
                FROM public_installment_offers
                WHERE eligible_for_ranking
                GROUP BY store_id
            ),
            discovery_stats AS (
                SELECT
                    store_id,
                    BOOL_OR(enabled) AS discovery_configured,
                    MAX(last_scan_at) AS latest_catalog_scan,
                    MAX(last_success_at) AS latest_catalog_success,
                    (ARRAY_AGG(last_error_code ORDER BY updated_at DESC)
                        FILTER (WHERE last_error_code IS NOT NULL))[1]
                        AS latest_catalog_error
                FROM discovery_sources
                WHERE enabled
                GROUP BY store_id
            )
            SELECT
                s.store_id, s.name, s.base_url, s.primary_category,
                s.coverage_categories, s.store_type, s.public_price_status,
                s.online_purchase, s.verification_confidence,
                s.active,
                (
                    COALESCE(s.registry_status, '') <> 'نشط/كتالوج فقط'
                    AND COALESCE(s.public_price_status, '') <> 'كتالوج فقط'
                    AND COALESCE(s.online_purchase, '') <> 'لا'
                ) AS price_capable,
                COALESCE(m.mapping_count, 0) AS mapping_count,
                COALESCE(m.mapped_product_count, 0) AS mapped_product_count,
                COALESCE(c.cash_offer_count, 0) AS cash_offer_count,
                COALESCE(c.verified_cash_offer_count, 0) AS verified_cash_offer_count,
                COALESCE(c.review_cash_offer_count, 0) AS review_cash_offer_count,
                COALESCE(c.priced_product_count, 0) AS priced_product_count,
                COALESCE(c.verified_priced_product_count, 0) AS verified_priced_product_count,
                COALESCE(c.review_priced_product_count, 0) AS review_priced_product_count,
                COALESCE(i.installment_plan_count, 0) AS installment_plan_count,
                c.latest_cash_update,
                d.latest_catalog_scan,
                d.latest_catalog_success,
                d.latest_catalog_error,
                COALESCE(d.discovery_configured, FALSE) AS discovery_configured,
                (COALESCE(m.mapping_count, 0) > 0) AS connected,
                (COALESCE(c.cash_offer_count, 0) > 0) AS priced
            FROM stores s
            LEFT JOIN mapping_stats m ON m.store_id = s.store_id
            LEFT JOIN cash_stats c ON c.store_id = s.store_id
            LEFT JOIN installment_stats i ON i.store_id = s.store_id
            LEFT JOIN discovery_stats d ON d.store_id = s.store_id
            WHERE (CAST(%s AS TEXT) IS NULL OR s.name ILIKE '%%' || CAST(%s AS TEXT) || '%%')
            ORDER BY
                s.active DESC,
                (COALESCE(c.cash_offer_count, 0) > 0) DESC,
                (COALESCE(m.mapping_count, 0) > 0) DESC,
                COALESCE(c.priced_product_count, 0) DESC,
                s.name
            LIMIT %s
            """,
            (query, query, limit),
        ).fetchall()
        total = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM stores
            WHERE (CAST(%s AS TEXT) IS NULL OR name ILIKE '%%' || CAST(%s AS TEXT) || '%%')
            """,
            (query, query),
        ).fetchone()["total"]
    items = []
    for row in rows:
        item = dict(row)
        for field in (
            "latest_cash_update",
            "latest_catalog_scan",
            "latest_catalog_success",
        ):
            if item.get(field) is not None:
                item[field] = item[field].isoformat()
        if not item.get("price_capable"):
            item["coverage_stage"] = "catalog_only"
        elif item.get("priced"):
            item["coverage_stage"] = "live_price"
        elif item.get("connected"):
            item["coverage_stage"] = "linked_waiting_price"
        elif item.get("latest_catalog_success"):
            item["coverage_stage"] = "discovered_waiting_match"
        elif item.get("latest_catalog_scan"):
            item["coverage_stage"] = "discovery_failed"
        elif item.get("discovery_configured"):
            item["coverage_stage"] = "pending_discovery"
        else:
            item["coverage_stage"] = "connector_missing"
        items.append(item)
    return {"items": items, "total": int(total or 0), "limit": limit}
