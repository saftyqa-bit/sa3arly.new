from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.db import connection, transaction


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _percent_or_none(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round(max(0.0, min(100.0, numerator / denominator * 100.0)), 3)


def calculate_store_quality(store_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        store = conn.execute(
            "SELECT store_id, name, active FROM stores WHERE store_id = %s",
            (store_id,),
        ).fetchone()
        if not store:
            return None
        row = conn.execute(
            """
            WITH mapping AS (
                SELECT
                    COUNT(*) FILTER (WHERE active) AS active_mappings,
                    COUNT(*) FILTER (
                        WHERE active AND (
                            NULLIF(direct_product_url, '') IS NOT NULL
                            OR LOWER(COALESCE(url_type, '')) IN (
                                'product', 'product_page', 'direct_product',
                                'رابط منتج مباشر مكتشف'
                            )
                        )
                    ) AS correct_destinations
                FROM listings
                WHERE store_id = %s
            ),
            cash AS (
                SELECT
                    COUNT(*) FILTER (WHERE cash_price IS NOT NULL) AS priced_samples,
                    COUNT(*) FILTER (
                        WHERE cash_price IS NOT NULL
                          AND anomaly_status = 'clear'
                          AND LOWER(COALESCE(review_status, '')) NOT IN (
                              'needs_review', 'rejected'
                          )
                    ) AS clean_prices,
                    COUNT(*) FILTER (
                        WHERE cash_price IS NOT NULL
                          AND LOWER(COALESCE(availability, '')) NOT IN ('', 'unknown')
                    ) AS clear_availability,
                    COUNT(*) FILTER (
                        WHERE cash_price IS NOT NULL
                          AND (
                              NULLIF(warranty_type, '') IS NOT NULL
                              OR NULLIF(warranty_provider, '') IS NOT NULL
                              OR warranty_months IS NOT NULL
                          )
                    ) AS clear_warranty,
                    COUNT(*) FILTER (
                        WHERE cash_price IS NOT NULL
                          AND last_success_at >= NOW() - INTERVAL '36 hours'
                    ) AS fresh_prices,
                    COUNT(*) FILTER (
                        WHERE cash_price IS NOT NULL
                          AND consecutive_failures >= 3
                    ) AS failing_links,
                    MAX(last_success_at) AS latest_success
                FROM current_offers
                WHERE store_id = %s
            ),
            task_health AS (
                SELECT
                    COUNT(*) FILTER (
                        WHERE completed_at >= NOW() - INTERVAL '90 days'
                    ) AS task_samples,
                    COUNT(*) FILTER (
                        WHERE completed_at >= NOW() - INTERVAL '90 days'
                          AND status = 'success'
                    ) AS successful_tasks,
                    COUNT(*) FILTER (
                        WHERE completed_at >= NOW() - INTERVAL '90 days'
                          AND error_code IN (
                              'http_404', 'http_410', 'wrong_page',
                              'product_not_found', 'invalid_destination'
                          )
                    ) AS broken_tasks
                FROM price_tasks
                WHERE store_id = %s
            ),
            reports AS (
                SELECT
                    COUNT(*) AS report_count,
                    COUNT(*) FILTER (
                        WHERE status IN ('confirmed', 'resolved')
                    ) AS actionable_reports,
                    COUNT(*) FILTER (
                        WHERE status = 'resolved'
                          AND resolved_at <= created_at + INTERVAL '72 hours'
                    ) AS resolved_within_72h,
                    COUNT(*) FILTER (
                        WHERE report_type = 'broken_link'
                          AND status IN ('confirmed', 'resolved')
                    ) AS confirmed_broken_links
                FROM price_reports
                WHERE store_id = %s
                  AND created_at >= NOW() - INTERVAL '90 days'
            )
            SELECT mapping.*, cash.*, task_health.*, reports.*
            FROM mapping, cash, task_health, reports
            """,
            (store_id, store_id, store_id, store_id),
        ).fetchone()

    metrics = dict(row)
    priced = float(metrics.get("priced_samples") or 0)
    mappings = float(metrics.get("active_mappings") or 0)
    tasks = float(metrics.get("task_samples") or 0)
    reports = float(metrics.get("report_count") or 0)
    actionable = float(metrics.get("actionable_reports") or 0)
    broken = float(metrics.get("failing_links") or 0) + float(
        metrics.get("broken_tasks") or 0
    ) + float(metrics.get("confirmed_broken_links") or 0)
    link_evidence = mappings + tasks

    price_accuracy = _percent_or_none(
        float(metrics.get("clean_prices") or 0), priced
    )
    update_regularity = _percent_or_none(
        float(metrics.get("fresh_prices") or 0) * 0.55
        + float(metrics.get("successful_tasks") or 0) * 0.45,
        priced * 0.55 + tasks * 0.45,
    )
    availability_clarity = _percent_or_none(
        float(metrics.get("clear_availability") or 0), priced
    )
    warranty_clarity = _percent_or_none(
        float(metrics.get("clear_warranty") or 0), priced
    )
    correct_destination = _percent_or_none(
        float(metrics.get("correct_destinations") or 0), mappings
    )
    broken_link_rate = (
        round(max(0.0, min(1.0, broken / link_evidence)), 4)
        if link_evidence > 0
        else None
    )
    complaint_response = _percent_or_none(
        float(metrics.get("resolved_within_72h") or 0), actionable
    )
    sample_size = int(max(priced, mappings, tasks, reports))
    evidence = {
        "priced_samples": int(priced),
        "active_mappings": int(mappings),
        "task_samples": int(tasks),
        "report_count": int(reports),
        "actionable_report_count": int(actionable),
        "calculated_at": datetime.now(UTC).isoformat(),
        "method": "separate evidence-based indicators; unknown stays null; no star rating",
    }

    with transaction() as conn:
        saved = conn.execute(
            """
            INSERT INTO store_quality_metrics (
                store_id, price_accuracy_score, update_regularity_score,
                availability_clarity_score, warranty_clarity_score,
                correct_destination_score, broken_link_rate,
                complaint_response_score, sample_size,
                calculation_window_days, evidence, calculated_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, 90, %s::jsonb, NOW(), NOW()
            )
            ON CONFLICT (store_id) DO UPDATE
            SET price_accuracy_score = EXCLUDED.price_accuracy_score,
                update_regularity_score = EXCLUDED.update_regularity_score,
                availability_clarity_score = EXCLUDED.availability_clarity_score,
                warranty_clarity_score = EXCLUDED.warranty_clarity_score,
                correct_destination_score = EXCLUDED.correct_destination_score,
                broken_link_rate = EXCLUDED.broken_link_rate,
                complaint_response_score = EXCLUDED.complaint_response_score,
                sample_size = EXCLUDED.sample_size,
                calculation_window_days = EXCLUDED.calculation_window_days,
                evidence = EXCLUDED.evidence,
                calculated_at = NOW(),
                updated_at = NOW()
            RETURNING *
            """,
            (
                store_id,
                price_accuracy,
                update_regularity,
                availability_clarity,
                warranty_clarity,
                correct_destination,
                broken_link_rate,
                complaint_response,
                sample_size,
                __import__("json").dumps(evidence),
            ),
        ).fetchone()
    return _jsonable({"store": dict(store), "metrics": dict(saved)})


def get_store_quality(store_id: str, *, recalculate: bool = True) -> dict[str, Any] | None:
    if recalculate:
        return calculate_store_quality(store_id)
    with connection() as conn:
        store = conn.execute(
            "SELECT store_id, name, active FROM stores WHERE store_id = %s",
            (store_id,),
        ).fetchone()
        metrics = conn.execute(
            "SELECT * FROM store_quality_metrics WHERE store_id = %s",
            (store_id,),
        ).fetchone()
    if not store:
        return None
    return _jsonable(
        {"store": dict(store), "metrics": dict(metrics) if metrics else None}
    )


def refresh_store_quality_if_stale(
    store_id: str,
    *,
    max_age_hours: int = 6,
) -> dict[str, Any] | None:
    cached = get_store_quality(store_id, recalculate=False)
    raw_metrics = cached.get("metrics") if cached else None
    calculated_at = raw_metrics.get("calculated_at") if isinstance(raw_metrics, dict) else None
    if calculated_at:
        timestamp = datetime.fromisoformat(str(calculated_at).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        if datetime.now(UTC) - timestamp.astimezone(UTC) <= timedelta(
            hours=max(1, max_age_hours)
        ):
            return cached
    return calculate_store_quality(store_id)
