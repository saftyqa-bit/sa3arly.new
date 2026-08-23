from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.db import connection
from app.repository_provider import repository
from app.settings import get_settings

logger = logging.getLogger(__name__)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _purchase_label(current: float | None, average: float | None, low: float | None) -> str | None:
    if current is None or average is None or average <= 0:
        return None
    if low is not None and current <= low * 1.02:
        return "قريب من أقل سعر خلال 90 يومًا"
    percent = (current - average) / average * 100
    if percent <= -7:
        return f"أقل من متوسط 90 يومًا بـ {abs(percent):.0f}%"
    if percent <= 3:
        return "قريب من متوسط 90 يومًا"
    if percent <= 10:
        return f"أعلى من متوسط 90 يومًا بـ {percent:.0f}%"
    return "السعر أعلى من المعتاد"


def _attach_history(conn, items: list[dict[str, Any]]) -> None:
    variant_ids = [str(item["variant_id"]) for item in items]
    if not variant_ids:
        return
    rows = conn.execute(
        """
        WITH observations AS (
            SELECT
                variant_id,
                store_id,
                observed_at,
                COALESCE(
                    total_price,
                    cash_price + COALESCE(shipping_cost, 0),
                    cash_price
                ) AS observed_price
            FROM offer_observations
            WHERE variant_id = ANY(%s)
              AND observed_at >= NOW() - INTERVAL '90 days'
              AND COALESCE(total_price, cash_price) >= 10
        ),
        daily AS (
            SELECT variant_id, observed_at::date AS day, MIN(observed_price) AS price
            FROM observations
            WHERE observed_at >= NOW() - INTERVAL '30 days'
            GROUP BY variant_id, observed_at::date
        ),
        spark AS (
            SELECT
                variant_id,
                JSONB_AGG(
                    JSONB_BUILD_OBJECT('date', day, 'price', price)
                    ORDER BY day
                ) AS sparkline
            FROM daily
            GROUP BY variant_id
        )
        SELECT
            o.variant_id,
            MIN(o.observed_price) FILTER (
                WHERE o.observed_at >= NOW() - INTERVAL '30 days'
            ) AS lowest_30d,
            MIN(o.observed_price) AS lowest_90d,
            AVG(o.observed_price) AS average_90d,
            MAX(o.observed_price) AS highest_90d,
            COUNT(*) AS observation_count,
            COUNT(DISTINCT o.store_id) AS store_count,
            COALESCE(s.sparkline, '[]'::jsonb) AS sparkline
        FROM observations o
        LEFT JOIN spark s ON s.variant_id = o.variant_id
        GROUP BY o.variant_id, s.sparkline
        """,
        (variant_ids,),
    ).fetchall()
    history = {str(row["variant_id"]): dict(row) for row in rows}
    for item in items:
        stats = history.get(str(item["variant_id"]), {})
        current = (
            item.get("lowest_final_cost")
            or item.get("lowest_delivered_total")
            or item.get("lowest_confirmed_cash_price")
        )
        item["price_history"] = {
            "lowest_30d": stats.get("lowest_30d"),
            "lowest_90d": stats.get("lowest_90d"),
            "average_90d": stats.get("average_90d"),
            "highest_90d": stats.get("highest_90d"),
            "sparkline": stats.get("sparkline") or [],
            "observation_count": int(stats.get("observation_count") or 0),
            "store_count": int(stats.get("store_count") or 0),
        }
        item["purchase_label"] = (
            _purchase_label(
                float(current) if current is not None else None,
                float(stats["average_90d"]) if stats.get("average_90d") is not None else None,
                float(stats["lowest_90d"]) if stats.get("lowest_90d") is not None else None,
            )
            if not item.get("cash_price_review_required")
            and int(stats.get("observation_count") or 0) >= 2
            and int(stats.get("store_count") or 0) >= 2
            else None
        )


def list_priced_products(
    *,
    mode: str = "cash",
    limit: int = 24,
    offset: int = 0,
    query: str | None = None,
    section: str | None = None,
) -> dict[str, Any]:
    """Return a true-cost-sorted, paginated public product directory."""
    if mode not in {"cash", "installment"}:
        raise ValueError(f"Unsupported mode: {mode}")
    limit = max(1, min(int(limit), 100))
    offset = max(0, int(offset))
    query = (query or "").strip()[:160] or None
    section = (section or "").strip()[:120] or None

    if get_settings().persistence_backend == "firestore":
        native = getattr(repository, "list_priced_products", None)
        if callable(native):
            return native(mode=mode, limit=limit, offset=offset)
        count_field = "cash_offer_count" if mode == "cash" else "installment_plan_count"
        price_field = "lowest_delivered_total" if mode == "cash" else "lowest_periodic_payment"
        items = repository._attach_summaries(repository._catalog_items())
        priced = [row for row in items if (row.get(count_field) or 0) > 0]
        if query:
            needle = query.casefold()
            priced = [
                row for row in priced
                if needle in " ".join(
                    str(row.get(key) or "")
                    for key in ("canonical_name", "brand", "model", "variant_name")
                ).casefold()
            ]
        if section:
            priced = [row for row in priced if str(row.get("section") or "") == section]
        priced.sort(
            key=lambda row: (
                row.get(price_field) is None,
                float(row.get(price_field) or 0),
                str(row.get("canonical_name") or ""),
            )
        )
        for row in priced:
            row.setdefault("price_history", {"sparkline": []})
            row.setdefault("purchase_label", None)
        return {
            "items": priced[offset : offset + limit],
            "total": len(priced),
            "limit": limit,
            "offset": offset,
        }

    if mode == "cash":
        offer_cte = """
            SELECT variant_id,
                   MIN(cash_price) AS lowest_cash_price,
                   MIN(cash_price) FILTER (
                       WHERE eligible_for_ranking
                   ) AS lowest_confirmed_cash_price,
                   MIN(COALESCE(comparable_total, cash_price)) FILTER (
                       WHERE eligible_for_ranking
                   ) AS lowest_final_cost,
                   MIN(comparable_total) FILTER (
                       WHERE eligible_for_ranking
                   ) AS lowest_delivered_total,
                   COUNT(*) AS cash_offer_count,
                   COUNT(*) FILTER (
                       WHERE eligible_for_ranking
                   ) AS confirmed_cash_offer_count,
                   COUNT(*) FILTER (
                       WHERE NOT eligible_for_ranking
                   ) AS review_cash_offer_count,
                   0::bigint AS installment_plan_count,
                   0::bigint AS confirmed_installment_plan_count,
                   0::bigint AS review_installment_plan_count,
                   NULL::numeric AS lowest_periodic_payment,
                   NULL::numeric AS lowest_installment_total,
                   NULL::numeric AS lowest_visible_periodic_payment,
                   NULL::numeric AS lowest_visible_installment_total
            FROM public_cash_offers
            WHERE cash_price >= 10
            GROUP BY variant_id
        """
        order_clause = (
            "confirmed_cash_offer_count DESC, "
            "COALESCE(lowest_final_cost, lowest_confirmed_cash_price, "
            "lowest_cash_price) ASC NULLS LAST"
        )
    else:
        offer_cte = """
            SELECT variant_id,
                   NULL::numeric AS lowest_cash_price,
                   NULL::numeric AS lowest_confirmed_cash_price,
                   NULL::numeric AS lowest_final_cost,
                   NULL::numeric AS lowest_delivered_total,
                   0::bigint AS cash_offer_count,
                   0::bigint AS confirmed_cash_offer_count,
                   0::bigint AS review_cash_offer_count,
                   COUNT(*) AS installment_plan_count,
                   COUNT(*) FILTER (
                       WHERE eligible_for_ranking
                   ) AS confirmed_installment_plan_count,
                   COUNT(*) FILTER (
                       WHERE NOT eligible_for_ranking
                   ) AS review_installment_plan_count,
                   MIN(periodic_payment) FILTER (
                       WHERE eligible_for_ranking
                   ) AS lowest_periodic_payment,
                   MIN(normalized_total) FILTER (
                       WHERE eligible_for_ranking
                   ) AS lowest_installment_total,
                   MIN(periodic_payment) AS lowest_visible_periodic_payment,
                   MIN(normalized_total) AS lowest_visible_installment_total
            FROM public_installment_offers
            WHERE periodic_payment IS NOT NULL
               OR normalized_total IS NOT NULL
            GROUP BY variant_id
        """
        order_clause = (
            "confirmed_installment_plan_count DESC, "
            "COALESCE(lowest_installment_total, lowest_visible_installment_total, "
            "lowest_periodic_payment, lowest_visible_periodic_payment) ASC NULLS LAST"
        )
    count_condition = "cash_offer_count > 0" if mode == "cash" else "installment_plan_count > 0"
    with connection() as conn:
        total = conn.execute(
            f"""
            WITH offers AS ({offer_cte}),
            summary AS (
                SELECT p.variant_id,
                       COALESCE(o.cash_offer_count, 0) AS cash_offer_count,
                       COALESCE(o.installment_plan_count, 0) AS installment_plan_count
                FROM variants p
                LEFT JOIN offers o ON o.variant_id = p.variant_id
                WHERE p.active
                  AND p.source_status <> 'catalog_provisional'
                  AND (CAST(%s AS TEXT) IS NULL OR CONCAT_WS(
                      ' ', p.canonical_name, p.brand, p.model, p.variant_name
                  ) ILIKE '%%' || %s || '%%')
                  AND (CAST(%s AS TEXT) IS NULL OR p.section = %s)
            )
            SELECT COUNT(*) AS total FROM summary WHERE {count_condition}
            """,
            (query, query, section, section),
        ).fetchone()["total"]
        rows = conn.execute(
            f"""
            WITH offers AS ({offer_cte}),
            summary AS (
                SELECT
                    p.variant_id, p.canonical_name, p.section, p.product_type,
                    p.brand, p.model, p.variant_name, p.image_url,
                    o.lowest_cash_price,
                    o.lowest_confirmed_cash_price,
                    o.lowest_final_cost,
                    o.lowest_delivered_total,
                    COALESCE(o.cash_offer_count, 0) AS cash_offer_count,
                    COALESCE(o.confirmed_cash_offer_count, 0)
                        AS confirmed_cash_offer_count,
                    COALESCE(o.review_cash_offer_count, 0)
                        AS review_cash_offer_count,
                    (
                        COALESCE(o.confirmed_cash_offer_count, 0) = 0
                        AND COALESCE(o.review_cash_offer_count, 0) > 0
                    ) AS cash_price_review_required,
                    COALESCE(o.installment_plan_count, 0) AS installment_plan_count,
                    COALESCE(o.confirmed_installment_plan_count, 0)
                        AS confirmed_installment_plan_count,
                    COALESCE(o.review_installment_plan_count, 0)
                        AS review_installment_plan_count,
                    (
                        COALESCE(o.confirmed_installment_plan_count, 0) = 0
                        AND COALESCE(o.review_installment_plan_count, 0) > 0
                    ) AS installment_price_review_required,
                    o.lowest_periodic_payment,
                    o.lowest_installment_total,
                    o.lowest_visible_periodic_payment,
                    o.lowest_visible_installment_total
                FROM variants p
                LEFT JOIN offers o ON o.variant_id = p.variant_id
                WHERE p.active
                  AND p.source_status <> 'catalog_provisional'
                  AND (CAST(%s AS TEXT) IS NULL OR CONCAT_WS(
                      ' ', p.canonical_name, p.brand, p.model, p.variant_name
                  ) ILIKE '%%' || %s || '%%')
                  AND (CAST(%s AS TEXT) IS NULL OR p.section = %s)
            )
            SELECT *
            FROM summary
            WHERE {count_condition}
            ORDER BY {order_clause}, canonical_name ASC
            LIMIT %s OFFSET %s
            """,
            (query, query, section, section, limit, offset),
        ).fetchall()
        items = [dict(row) for row in rows]
        try:
            _attach_history(conn, items)
        except Exception:
            logger.exception(
                "Price history unavailable; serving the priced product directory without it"
            )
            for item in items:
                item["price_history"] = {
                    "lowest_30d": None,
                    "lowest_90d": None,
                    "average_90d": None,
                    "highest_90d": None,
                    "sparkline": [],
                    "observation_count": 0,
                    "store_count": 0,
                }
                item["purchase_label"] = None
    return {
        "items": [_jsonable(item) for item in items],
        "total": int(total or 0),
        "limit": limit,
        "offset": offset,
    }
