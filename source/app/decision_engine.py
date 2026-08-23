from __future__ import annotations

import hashlib
import logging
import math
import re
import statistics
import unicodedata
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from app.db import connection, transaction
from app.repository_provider import repository

ComparisonMode = Literal["cheapest", "safest", "fastest", "installment"]
MIN_PUBLIC_CASH_PRICE_EGP = 10.0
logger = logging.getLogger(__name__)

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
SEARCH_ALIASES = {
    "ايفون": "iphone",
    "آيفون": "iphone",
    "أيفون": "iphone",
    "سامسونج": "samsung",
    "شاومي": "xiaomi",
    "ريدمي": "redmi",
    "هونر": "honor",
    "هواوي": "huawei",
    "اوبو": "oppo",
    "أوبو": "oppo",
    "ريلمي": "realme",
    "ماك بوك": "macbook",
    "لاب توب": "laptop",
    "لابتوب": "laptop",
    "برو ماكس": "pro max",
    "الترا": "ultra",
    "جيجا بايت": "gb",
    "جيجابايت": "gb",
    "جيجا": "gb",
    "رامات": "ram",
    "رام": "ram",
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 3)


def normalize_arabic_search(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value.translate(ARABIC_DIGITS))
    normalized = re.sub(r"[\u064b-\u065f\u0670]", "", normalized)
    normalized = normalized.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    normalized = normalized.replace("ى", "ي").replace("ة", "ه")
    normalized = normalized.casefold()
    normalized = re.sub(r"(?<=\d)\s*(?:gb|g|جيجا)\b", " gb", normalized)
    normalized = re.sub(r"[-_/]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    for alias in sorted(SEARCH_ALIASES, key=len, reverse=True):
        normalized = normalized.replace(alias, SEARCH_ALIASES[alias])
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def cash_final_cost(offer: dict[str, Any]) -> float | None:
    price = _number(offer.get("cash_price"))
    if price is None or price < MIN_PUBLIC_CASH_PRICE_EGP:
        return None
    for field in ("final_cost", "comparable_total", "total_price"):
        published_total = _number(offer.get(field))
        if published_total is not None and published_total >= MIN_PUBLIC_CASH_PRICE_EGP:
            return round(published_total, 2)
    return round(
        max(
            price
            + (_number(offer.get("shipping_cost")) or 0)
            + (_number(offer.get("mandatory_fees")) or 0)
            + (_number(offer.get("card_fees")) or 0)
            - (_number(offer.get("coupon_discount")) or 0),
            0,
        ),
        2,
    )


def installment_final_cost(plan: dict[str, Any]) -> float | None:
    published = _number(plan.get("total_published"))
    calculated = _number(plan.get("total_calculated"))
    if published and published > 0:
        return published
    if calculated and calculated > 0:
        return calculated
    periodic = _number(plan.get("periodic_payment"))
    months = _number(plan.get("months"))
    if periodic is None or months is None or periodic <= 0 or months <= 0:
        return None
    return round(
        max(
            (_number(plan.get("down_payment")) or 0)
            + periodic * months
            + (_number(plan.get("admin_fees")) or 0)
            + (_number(plan.get("processing_fees")) or 0)
            + (_number(plan.get("insurance_fees")) or 0)
            + (_number(plan.get("other_fees")) or 0)
            + (_number(plan.get("card_fees")) or 0)
            + (_number(plan.get("shipping_cost")) or 0)
            - (_number(plan.get("coupon_discount")) or 0),
            0,
        ),
        2,
    )


def _freshness_score(value: Any) -> float:
    if not value:
        return 25.0
    timestamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    hours = max((datetime.now(UTC) - timestamp.astimezone(UTC)).total_seconds() / 3600, 0)
    if hours <= 6:
        return 100.0
    if hours <= 24:
        return 90.0
    if hours <= 48:
        return 72.0
    if hours <= 96:
        return 48.0
    return 20.0


def _availability_score(value: Any) -> float:
    key = str(value or "unknown").lower()
    return {
        "available": 100.0,
        "in_stock": 100.0,
        "limited": 82.0,
        "preorder": 55.0,
        "unknown": 38.0,
        "out_of_stock": 0.0,
        "unavailable": 0.0,
    }.get(key, 38.0)


def _warranty_score(offer: dict[str, Any]) -> float:
    months = _number(offer.get("warranty_months")) or 0
    warranty_type = str(offer.get("warranty_type") or "").lower()
    provider = str(offer.get("warranty_provider") or "").strip()
    score = 15.0
    if months >= 12:
        score += 35
    elif months > 0:
        score += 20
    if "official" in warranty_type or "manufacturer" in warranty_type or "رسمي" in warranty_type:
        score += 35
    elif warranty_type:
        score += 20
    if provider:
        score += 15
    return _clamp(score)


def _store_score(offer: dict[str, Any]) -> float:
    values = [
        _number(offer.get("price_accuracy_score")),
        _number(offer.get("update_regularity_score")),
        _number(offer.get("availability_clarity_score")),
        _number(offer.get("warranty_clarity_score")),
        _number(offer.get("correct_destination_score")),
    ]
    known = [value for value in values if value is not None]
    base = statistics.fmean(known) if known else 55.0
    if offer.get("store_verified"):
        base += 10
    if offer.get("seller_verified"):
        base += 5
    broken = _number(offer.get("broken_link_rate"))
    if broken is not None:
        base -= broken * 30
    return _clamp(base)


def _match_score(offer: dict[str, Any]) -> float:
    explicit = _number(offer.get("match_quality_score"))
    if explicit is not None:
        return _clamp(explicit)
    confidence = str(offer.get("match_confidence") or "").lower()
    return {
        "high": 95.0,
        "medium": 72.0,
        "low": 38.0,
        "ambiguous": 20.0,
    }.get(confidence, 55.0)


def _delivery_score(offer: dict[str, Any]) -> float:
    minimum = _number(offer.get("min_delivery_days"))
    maximum = _number(offer.get("max_delivery_days"))
    if offer.get("pickup_available"):
        return 100.0
    days = maximum if maximum is not None else minimum
    if days is None:
        return 40.0
    if days <= 1:
        return 96.0
    if days <= 2:
        return 86.0
    if days <= 4:
        return 70.0
    if days <= 7:
        return 48.0
    return 25.0


def _price_position(final_cost: float | None, history: dict[str, Any]) -> dict[str, Any]:
    if final_cost is None:
        return {"label": "السعر غير مكتمل", "tone": "unknown", "percent_vs_average": None}
    if not history.get("sufficient_for_recommendation"):
        return {
            "label": "بيانات غير كافية لتوصية شراء",
            "tone": "unknown",
            "percent_vs_average": None,
        }
    average = _number(history.get("average_90d"))
    low = _number(history.get("lowest_90d"))
    if average is None or average <= 0:
        return {"label": "سعر حالي موثّق", "tone": "neutral", "percent_vs_average": None}
    percent = round((final_cost - average) / average * 100, 2)
    if low and final_cost <= low * 1.02:
        label, tone = "فرصة شراء ممتازة", "excellent"
    elif percent <= -7:
        label, tone = "فرصة شراء جيدة", "good"
    elif percent <= 3:
        label, tone = "السعر قريب من المتوسط", "fair"
    elif percent <= 10:
        label, tone = "السعر أعلى قليلًا من المعتاد", "high"
    else:
        label, tone = "يفضل الانتظار أو مقارنة بديل", "expensive"
    return {"label": label, "tone": tone, "percent_vs_average": percent}


def summarize_price_history(rows: list[dict[str, Any]]) -> dict[str, Any]:
    points: list[tuple[datetime, float, dict[str, Any]]] = []
    for row in rows:
        value = _number(row.get("observed_price"))
        observed = row.get("observed_at")
        if value is None or value < MIN_PUBLIC_CASH_PRICE_EGP or not observed:
            continue
        timestamp = observed if isinstance(observed, datetime) else datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
        points.append((timestamp, value, row))
    points.sort(key=lambda item: item[0])
    if not points:
        return {
            "lowest_30d": None,
            "lowest_90d": None,
            "average_90d": None,
            "highest_90d": None,
            "change_count": 0,
            "last_drop_at": None,
            "trend": "insufficient_data",
            "sparkline": [],
            "markers": [],
            "observation_count": 0,
            "store_count": 0,
            "day_count": 0,
            "sufficient_for_recommendation": False,
        }

    now = datetime.now(UTC)
    prices90 = [price for observed, price, _ in points if observed.astimezone(UTC) >= now - timedelta(days=90)]
    prices30 = [price for observed, price, _ in points if observed.astimezone(UTC) >= now - timedelta(days=30)]
    prices = prices90 or [item[1] for item in points]
    change_count = sum(1 for index in range(1, len(points)) if abs(points[index][1] - points[index - 1][1]) >= 0.01)
    last_drop = next(
        (points[index][0] for index in range(len(points) - 1, 0, -1) if points[index][1] < points[index - 1][1]),
        None,
    )

    daily: dict[str, float] = {}
    for observed, price, _ in points:
        key = observed.date().isoformat()
        daily[key] = min(daily.get(key, price), price)
    sparkline = [{"date": key, "price": daily[key]} for key in sorted(daily)[-30:]]

    mean = statistics.fmean(prices)
    deviation = statistics.pstdev(prices) if len(prices) > 1 else 0.0
    volatility = deviation / mean if mean else 0.0
    if len(sparkline) >= 2:
        first = sparkline[0]["price"]
        last = sparkline[-1]["price"]
        movement = (last - first) / first if first else 0.0
    else:
        movement = 0.0
    if volatility >= 0.08:
        trend = "volatile"
    elif movement <= -0.04:
        trend = "declining"
    elif movement >= 0.04:
        trend = "rising"
    else:
        trend = "stable"

    markers = []
    for observed, price, row in points:
        snapshot = row.get("snapshot") or {}
        coupon = snapshot.get("coupon_code") if isinstance(snapshot, dict) else None
        if coupon:
            markers.append({"date": observed.date().isoformat(), "type": "coupon", "label": str(coupon), "price": price})
    store_count = len(
        {
            str(row.get("store_id"))
            for _, _, row in points
            if row.get("store_id") not in (None, "")
        }
    )
    day_count = len({observed.date() for observed, _, _ in points})
    return {
        "lowest_30d": min(prices30) if prices30 else None,
        "lowest_90d": min(prices) if prices else None,
        "average_90d": round(mean, 2) if prices else None,
        "highest_90d": max(prices) if prices else None,
        "change_count": change_count,
        "last_drop_at": last_drop.isoformat() if last_drop else None,
        "trend": trend,
        "volatility": round(volatility, 4),
        "sparkline": sparkline,
        "markers": markers[-20:],
        "observation_count": len(points),
        "store_count": store_count,
        "day_count": day_count,
        "sufficient_for_recommendation": len(points) >= 2 and store_count >= 2,
    }


def _offer_explanation(offer: dict[str, Any], index: int, store_count: int) -> str:
    reasons: list[str] = []
    if index == 0:
        reasons.append("الأقل بعد الشحن والرسوم والكوبون")
    if _store_score(offer) >= 75:
        reasons.append("المتجر لديه مؤشرات جودة قوية")
    if _availability_score(offer.get("availability")) >= 80:
        reasons.append("التوفر مؤكد")
    if _warranty_score(offer) >= 70:
        reasons.append("الضمان واضح")
    if _freshness_score(offer.get("last_success_at")) >= 80:
        reasons.append("التحديث حديث")
    if _match_score(offer) >= 85:
        reasons.append("مطابقة النسخة قوية")
    if store_count >= 3:
        reasons.append(f"السعر مؤكد عبر سوق يضم {store_count} متاجر")
    return "، و".join(reasons[:4]) or "العرض منشور مع توضيح عناصر التكلفة والبيانات المتاحة"


def enrich_cash_offers(rows: list[dict[str, Any]], history: dict[str, Any]) -> list[dict[str, Any]]:
    active = [
        dict(row)
        for row in rows
        if str(row.get("anomaly_status") or "clear") != "blocked"
        and (_number(row.get("cash_price")) or 0) >= MIN_PUBLIC_CASH_PRICE_EGP
    ]
    for offer in active:
        offer["final_cost"] = cash_final_cost(offer)
        if offer.get("shipping_cost_known") is None:
            offer["shipping_cost_known"] = offer.get("shipping_cost") is not None
    active = [
        offer
        for offer in active
        if (_number(offer.get("final_cost")) or 0) >= MIN_PUBLIC_CASH_PRICE_EGP
    ]
    active.sort(
        key=lambda item: (
            str(item.get("anomaly_status") or "clear") != "clear",
            item.get("final_cost") if item.get("final_cost") is not None else math.inf,
        )
    )
    verified_costs = [
        item["final_cost"]
        for item in active
        if item.get("final_cost") is not None
        and str(item.get("anomaly_status") or "clear") == "clear"
    ]
    final_costs = verified_costs or [
        item["final_cost"] for item in active if item.get("final_cost") is not None
    ]
    minimum = min(final_costs) if final_costs else None
    for index, offer in enumerate(active):
        cost = offer.get("final_cost")
        price_score = 50.0 if minimum is None or cost is None else _clamp(100 - ((cost - minimum) / max(minimum, 1) * 140))
        components = {
            "price": price_score,
            "store": _store_score(offer),
            "shipping": 100.0 if (_number(offer.get("shipping_cost")) or 0) == 0 else 72.0,
            "availability": _availability_score(offer.get("availability")),
            "warranty": _warranty_score(offer),
            "freshness": _freshness_score(offer.get("last_success_at")),
            "match": _match_score(offer),
            "market_coverage": _clamp(len(active) * 18.0),
            "delivery": _delivery_score(offer),
        }
        decision_score = (
            components["price"] * 0.27
            + components["store"] * 0.15
            + components["shipping"] * 0.08
            + components["availability"] * 0.12
            + components["warranty"] * 0.10
            + components["freshness"] * 0.10
            + components["match"] * 0.13
            + components["market_coverage"] * 0.05
        )
        offer["decision_score"] = _clamp(decision_score)
        offer["verification_status"] = (
            "verified"
            if str(offer.get("anomaly_status") or "clear") == "clear"
            else "needs_review"
        )
        offer["safety_score"] = _clamp(
            components["store"] * 0.27
            + components["warranty"] * 0.22
            + components["availability"] * 0.18
            + components["freshness"] * 0.13
            + components["match"] * 0.20
        )
        offer["delivery_score"] = components["delivery"]
        offer["score_components"] = components
        offer["price_position"] = _price_position(cost, history)
        offer["explanation"] = _offer_explanation(offer, index, len(active))
        offer["match_evidence"] = {
            "mapping_id": offer.get("mapping_id"),
            "url": offer.get("source_url"),
            "store_sku": offer.get("store_sku"),
            "manufacturer_sku": offer.get("mapping_manufacturer_sku"),
            "title_as_seen": offer.get("title_as_seen"),
            "match_confidence": offer.get("match_confidence"),
            "fields": [
                key
                for key, present in {
                    "model": bool(offer.get("title_as_seen")),
                    "store_sku": bool(offer.get("store_sku")),
                    "manufacturer_sku": bool(offer.get("mapping_manufacturer_sku")),
                    "variant": (_match_score(offer) >= 70),
                }.items()
                if present
            ],
        }
    return [_jsonable(item) for item in active]


def enrich_installment_plans(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        if str(row.get("anomaly_status") or "clear") == "blocked":
            continue
        plan = dict(row)
        months = _number(plan.get("months"))
        if months is None or months <= 0:
            continue
        plan["final_installment_cost"] = installment_final_cost(plan)
        if plan["final_installment_cost"] is None:
            continue
        key = (
            plan.get("store_id") or plan.get("store_name"),
            str(plan.get("provider_name") or "").casefold(),
            _number(plan.get("months")),
            _number(plan.get("periodic_payment")),
            _number(plan.get("down_payment")) or 0,
            plan["final_installment_cost"],
        )
        if key in seen:
            continue
        seen.add(key)
        plans.append(plan)
    plans.sort(key=lambda item: item.get("final_installment_cost") if item.get("final_installment_cost") is not None else math.inf)
    for index, plan in enumerate(plans):
        total = plan.get("final_installment_cost")
        monthly = _number(plan.get("periodic_payment"))
        cash = _number(plan.get("cash_price_at_observation"))
        plan["lowest_total"] = index == 0 and total is not None
        plan["low_payment_high_total"] = bool(monthly and total and cash and monthly < cash * 0.08 and total > cash * 1.18)
        plan["explanation"] = (
            "أقل إجمالي مدفوع بعد المقدم والأقساط والرسوم والشحن"
            if index == 0
            else "مرتب حسب إجمالي المدفوع الحقيقي، وليس قيمة القسط وحدها"
        )
    return [_jsonable(item) for item in plans]


def _load_history(variant_id: str) -> dict[str, Any]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT store_id, observed_at,
                   COALESCE(total_price, cash_price + COALESCE(shipping_cost, 0), cash_price) AS observed_price,
                   change_type, snapshot
            FROM offer_observations
            WHERE variant_id = %s
              AND observed_at >= NOW() - INTERVAL '90 days'
              AND COALESCE(total_price, cash_price) >= %s
            ORDER BY observed_at
            """,
            (variant_id, MIN_PUBLIC_CASH_PRICE_EGP),
        ).fetchall()
    return summarize_price_history([dict(row) for row in rows])


def _cash_rows(variant_id: str) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM cash_decision_inputs
            WHERE variant_id = %s
            ORDER BY final_cost NULLS LAST, store_name
            """,
            (variant_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _installment_rows(variant_id: str) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM installment_decision_inputs
            WHERE variant_id = %s
              AND final_installment_cost IS NOT NULL
            ORDER BY final_installment_cost NULLS LAST, periodic_payment NULLS LAST
            """,
            (variant_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _spec_map(product: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    raw = product.get("specs") or {}
    if isinstance(raw, dict):
        result.update({str(key): str(value) for key, value in raw.items() if value not in (None, "")})
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("name") and item.get("value"):
                result[str(item["name"])] = str(item["value"])
    for source, label in (("ram_gb", "RAM"), ("storage_gb", "التخزين"), ("color", "اللون")):
        if product.get(source) not in (None, ""):
            result.setdefault(label, str(product[source]))
    return result


def _similarity(left: dict[str, str], right: dict[str, str]) -> float:
    keys = set(left) | set(right)
    if not keys:
        return 0.0
    compared = 0
    matched = 0.0
    for key in keys:
        if key not in left or key not in right:
            continue
        compared += 1
        lval = normalize_arabic_search(left[key])
        rval = normalize_arabic_search(right[key])
        if lval == rval:
            matched += 1
        elif lval in rval or rval in lval:
            matched += 0.65
    return matched / compared if compared else 0.0


def smart_alternatives(variant_id: str, limit: int = 12) -> list[dict[str, Any]]:
    with connection() as conn:
        current = conn.execute(
            """
            SELECT variant_id, product_id, category_id, brand_id, canonical_name,
                   brand, model, variant_name, ram_gb, storage_gb, color, specs
            FROM variants
            WHERE variant_id = %s AND active
            """,
            (variant_id,),
        ).fetchone()
        if not current:
            return []
        candidates = conn.execute(
            """
            SELECT v.variant_id, v.product_id, v.category_id, v.brand_id,
                   v.canonical_name, v.brand, v.model, v.variant_name,
                   v.ram_gb, v.storage_gb, v.color, v.specs,
                   MIN(c.final_cost) AS lowest_final_cost,
                   MIN(i.final_installment_cost) AS lowest_installment_total
            FROM variants v
            LEFT JOIN cash_decision_inputs c
              ON c.variant_id = v.variant_id
             AND c.cash_price >= 10
             AND c.final_cost >= 10
             AND c.anomaly_status = 'clear'
            LEFT JOIN installment_decision_inputs i ON i.variant_id = v.variant_id
            WHERE v.active
              AND v.variant_id <> %s
              AND v.category_id IS NOT DISTINCT FROM %s
            GROUP BY v.variant_id
            HAVING MIN(c.final_cost) IS NOT NULL OR MIN(i.final_installment_cost) IS NOT NULL
            LIMIT 120
            """,
            (variant_id, current.get("category_id")),
        ).fetchall()
    current_dict = dict(current)
    current_specs = _spec_map(current_dict)
    current_price = None
    try:
        cash = _cash_rows(variant_id)
        current_price = min((cash_final_cost(row) for row in cash if cash_final_cost(row) is not None), default=None)
    except Exception:
        current_price = None
    ranked = []
    for row in candidates:
        candidate = dict(row)
        similarity = _similarity(current_specs, _spec_map(candidate))
        price = _number(candidate.get("lowest_final_cost"))
        price_gap = None if current_price is None or price is None else price - current_price
        same_brand = candidate.get("brand_id") == current_dict.get("brand_id")
        if price_gap is not None and price_gap < 0 and similarity >= 0.45:
            kind = "cheaper_similar"
            reason = "بديل أرخص وقريب في المواصفات"
        elif price_gap is not None and 0 < price_gap <= max(current_price * 0.12, 1500) and similarity >= 0.55:
            kind = "small_upgrade"
            reason = "مواصفات قريبة أو أفضل بفارق سعري صغير"
        elif not same_brand and similarity >= 0.50:
            kind = "same_budget_other_brand"
            reason = "بديل من علامة أخرى ضمن ميزانية قريبة"
        elif same_brand and candidate.get("model") == current_dict.get("model"):
            kind = "different_capacity"
            reason = "نسخة مختلفة السعة قد توفر المال"
        else:
            kind = "similar"
            reason = "بديل قريب وفق المواصفات المتاحة"
        rank = similarity * 100 - (abs(price_gap) / max(current_price or 1, 1) * 30 if price_gap is not None else 10)
        candidate.update({"similarity_score": round(similarity * 100, 2), "price_gap": price_gap, "alternative_type": kind, "reason": reason, "rank": rank})
        ranked.append(candidate)
    ranked.sort(key=lambda item: item["rank"], reverse=True)
    return [_jsonable(item) for item in ranked[:limit]]


def get_purchase_decision(variant_id: str) -> dict[str, Any] | None:
    base = repository.get_product_comparison(variant_id, include_unpriced=True)
    if base is None:
        return None
    canonical_id = str(base.get("product", {}).get("variant_id") or base.get("product", {}).get("id") or variant_id)
    degraded_components: list[str] = []
    try:
        history = _load_history(canonical_id)
    except Exception:
        logger.exception(
            "Price history unavailable; serving the public comparison fallback",
            extra={"variant_id": canonical_id},
        )
        history = summarize_price_history([])
        degraded_components.append("history")
    try:
        decision_cash_rows = _cash_rows(canonical_id)
    except Exception:
        logger.exception(
            "Decision cash view unavailable; serving public cash offers",
            extra={"variant_id": canonical_id},
        )
        decision_cash_rows = [dict(row) for row in base.get("cash_offers") or []]
        degraded_components.append("cash_analysis")
    try:
        decision_installment_rows = _installment_rows(canonical_id)
    except Exception:
        logger.exception(
            "Decision installment view unavailable; serving public installment plans",
            extra={"variant_id": canonical_id},
        )
        decision_installment_rows = [
            dict(row) for row in base.get("installment_plans") or []
        ]
        degraded_components.append("installment_analysis")
    cash = enrich_cash_offers(decision_cash_rows, history)
    installments = enrich_installment_plans(decision_installment_rows)

    mode_orders = {
        "cheapest": [
            item["offer_id"]
            for item in sorted(
                cash,
                key=lambda item: (
                    str(item.get("anomaly_status") or "clear") != "clear",
                    item.get("final_cost") or math.inf,
                ),
            )
        ],
        "safest": [
            item["offer_id"]
            for item in sorted(
                cash,
                key=lambda item: (
                    str(item.get("anomaly_status") or "clear") == "clear",
                    item.get("safety_score") or 0,
                ),
                reverse=True,
            )
        ],
        "fastest": [
            item["offer_id"]
            for item in sorted(
                cash,
                key=lambda item: (
                    str(item.get("anomaly_status") or "clear") == "clear",
                    item.get("delivery_score") or 0,
                ),
                reverse=True,
            )
        ],
        "installment": [item["plan_id"] for item in sorted(installments, key=lambda item: item.get("final_installment_cost") or math.inf)],
    }
    best = cash[0] if cash else None
    purchase_index = best.get("price_position") if best else {"label": "لا يوجد سعر حي", "tone": "unknown", "percent_vs_average": None}
    recommendation_ready = bool(
        best
        and history.get("sufficient_for_recommendation")
        and str(best.get("anomaly_status") or "clear") == "clear"
        and str(best.get("availability") or "").lower() in {"available", "in_stock", "limited"}
        and _match_score(best) >= 70
    )
    if recommendation_ready and best:
        purchase_index = {
            **purchase_index,
            "score": best.get("decision_score"),
            "explanation": f"اخترنا هذا العرض لأنه {best.get('explanation')}",
            "best_offer_id": best.get("offer_id"),
        }
    elif best:
        purchase_index = {
            "label": "بيانات غير كافية لتوصية شراء",
            "tone": "unknown",
            "percent_vs_average": None,
            "explanation": "نعرض السعر المرصود للشفافية، لكننا لا نصدر توصية قبل تأكيده من أكثر من متجر ومعرفة التوفر ومطابقة النسخة.",
            "best_offer_id": best.get("offer_id"),
        }
    try:
        alternatives = smart_alternatives(canonical_id)
    except Exception:
        logger.exception(
            "Smart alternatives unavailable; serving the product decision without them",
            extra={"variant_id": canonical_id},
        )
        alternatives = []
        degraded_components.append("alternatives")
    return _jsonable(
        {
            "product": base.get("product"),
            "purchase_index": purchase_index,
            "history": history,
            "cash_offers": cash,
            "installment_plans": installments,
            "mode_orders": mode_orders,
            "mode_labels": {
                "cheapest": "أوفر سعر",
                "safest": "أضمن شراء",
                "fastest": "أسرع توصيل",
                "installment": "أفضل تقسيط",
            },
            "alternatives": alternatives,
            "known_store_count": len({item.get("store_id") for item in cash if item.get("store_id")}),
            "last_known_cash_price": base.get("last_known_cash_price"),
            "last_known_cash_price_at": base.get("last_known_cash_price_at"),
            "degraded_components": degraded_components,
        }
    )


def smart_search(query: str, *, limit: int = 20) -> dict[str, Any]:
    normalized = normalize_arabic_search(query)
    if not normalized:
        return {"query": query, "normalized_query": normalized, "items": [], "suggestion": None}
    like = f"%{normalized}%"
    try:
        with connection() as conn:
            rows = conn.execute(
                """
                WITH alias_scores AS (
                    SELECT COALESCE(a.variant_id, v.variant_id) AS variant_id,
                           MAX(SIMILARITY(a.normalized_alias, %s)) AS alias_score
                    FROM search_aliases a
                    LEFT JOIN variants v ON v.product_id = a.product_id AND v.active
                    WHERE a.active
                      AND (a.normalized_alias ILIKE %s OR SIMILARITY(a.normalized_alias, %s) > 0.18)
                    GROUP BY COALESCE(a.variant_id, v.variant_id)
                )
                SELECT v.variant_id, v.product_id, v.canonical_name, v.section,
                       v.product_type, v.brand, v.model, v.variant_name,
                       v.image_url,
                       v.ram_gb, v.storage_gb, v.color, v.manufacturer_sku, v.gtin,
                       v.specs,
                       GREATEST(
                           SIMILARITY(v.search_document, %s),
                           SIMILARITY(LOWER(v.canonical_name), %s),
                           COALESCE(a.alias_score, 0),
                           CASE WHEN LOWER(COALESCE(v.manufacturer_sku, '')) = %s THEN 1 ELSE 0 END,
                           CASE WHEN LOWER(COALESCE(v.gtin, '')) = %s THEN 1 ELSE 0 END
                       ) AS search_score
                FROM variants v
                LEFT JOIN alias_scores a ON a.variant_id = v.variant_id
                WHERE v.active
                  AND v.source_status <> 'catalog_provisional'
                  AND (
                      v.search_document ILIKE %s
                      OR SIMILARITY(v.search_document, %s) > 0.12
                      OR a.alias_score IS NOT NULL
                      OR LOWER(COALESCE(v.manufacturer_sku, '')) = %s
                      OR LOWER(COALESCE(v.gtin, '')) = %s
                  )
                ORDER BY search_score DESC, v.canonical_name
                LIMIT %s
                """,
                (
                    normalized,
                    like,
                    normalized,
                    normalized,
                    normalized,
                    normalized,
                    normalized,
                    normalized,
                    like,
                    normalized,
                    normalized,
                    normalized,
                    max(1, min(int(limit), 50)),
                ),
            ).fetchall()
    except Exception:
        logger.exception(
            "Smart search ranking unavailable; serving basic catalog search",
            extra={"search_query": normalized},
        )
        fallback = repository.search_products(
            normalized,
            limit=max(1, min(int(limit), 50)),
        )
        items = [
            {
                **dict(row),
                "search_score": _number(row.get("relevance")) or 0.0,
            }
            for row in fallback
        ]
        suggestion = items[0].get("canonical_name") if items else None
        return _jsonable(
            {
                "query": query,
                "normalized_query": normalized,
                "items": items,
                "suggestion": suggestion,
                "degraded": True,
            }
        )
    items = [_jsonable(dict(row)) for row in rows]
    suggestion = items[0]["canonical_name"] if items and items[0].get("search_score", 0) >= 0.25 else None
    return {"query": query, "normalized_query": normalized, "items": items, "suggestion": suggestion}


def compare_products(variant_ids: list[str]) -> dict[str, Any]:
    unique = list(dict.fromkeys(variant_ids))
    if not 2 <= len(unique) <= 4:
        raise ValueError("Choose between two and four distinct products")
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT v.variant_id, v.product_id, v.canonical_name, v.brand, v.model,
                   v.variant_name, v.ram_gb, v.storage_gb, v.color,
                   v.manufacturer_sku, v.specs,
                   MIN(c.final_cost) AS lowest_final_cost,
                   MIN(i.final_installment_cost) AS lowest_installment_total,
                   MAX(c.warranty_months) AS warranty_months,
                   COUNT(DISTINCT c.store_id) AS confirmed_store_count
            FROM variants v
            LEFT JOIN cash_decision_inputs c
              ON c.variant_id = v.variant_id
             AND c.cash_price >= 10
             AND c.final_cost >= 10
             AND c.anomaly_status = 'clear'
            LEFT JOIN installment_decision_inputs i ON i.variant_id = v.variant_id
            WHERE v.variant_id = ANY(%s)
            GROUP BY v.variant_id
            """,
            (unique,),
        ).fetchall()
    products = [dict(row) for row in rows]
    by_id = {item["variant_id"]: item for item in products}
    products = [by_id[item] for item in unique if item in by_id]
    if len(products) != len(unique):
        raise ValueError("One or more products were not found")
    spec_maps = {item["variant_id"]: _spec_map(item) for item in products}
    keys = sorted({key for mapping in spec_maps.values() for key in mapping})
    matrix = [
        {"name": key, "values": {item["variant_id"]: spec_maps[item["variant_id"]].get(key) for item in products}}
        for key in keys
    ]
    prices = [_number(item.get("lowest_final_cost")) for item in products]
    known_prices = [price for price in prices if price is not None and price > 0]
    max_price = max(known_prices) if known_prices else 1
    for item in products:
        price = _number(item.get("lowest_final_cost"))
        completeness = len(spec_maps[item["variant_id"]]) / max(len(keys), 1)
        store_score = min(int(item.get("confirmed_store_count") or 0) / 4, 1)
        price_score = 0 if price is None else 1 - price / max_price
        item["value_score"] = round((price_score * 45 + completeness * 35 + store_score * 20), 2)
        item["specs_normalized"] = spec_maps[item["variant_id"]]
    ordered = sorted(products, key=lambda item: item.get("value_score", 0), reverse=True)
    recommendation_ready = len(known_prices) == len(products) and all(
        int(item.get("confirmed_store_count") or 0) > 0 for item in products
    )
    explanation = None
    if recommendation_ready and len(ordered) >= 2:
        first, second = ordered[0], ordered[1]
        gap = (_number(first.get("lowest_final_cost")) or 0) - (_number(second.get("lowest_final_cost")) or 0)
        explanation = (
            f"{first['canonical_name']} يقدم قيمة أعلى وفق المواصفات والسعر المتاح"
            + (f" بفارق سعري {abs(gap):,.0f} جنيه" if gap else "")
            + "."
        )
    if not recommendation_ready:
        explanation = "المواصفات معروضة للمقارنة، لكن بيانات السعر غير مكتملة لإعلان أفضل قيمة."
    return _jsonable({
        "products": products,
        "matrix": matrix,
        "best_value_variant_id": ordered[0]["variant_id"] if recommendation_ready else None,
        "explanation": explanation,
    })


def create_alert_rule(payload: dict[str, Any]) -> dict[str, Any]:
    variant_id = str(payload.get("variant_id") or "").strip()
    rule_type = str(payload.get("rule_type") or "").strip()
    channel = str(payload.get("channel") or "local").strip()
    allowed_rules = {
        "below_amount", "at_90_day_low", "interest_free_installment",
        "store_available", "back_in_stock", "coupon_available",
        "final_cost_drop", "weekly_wishlist_digest",
    }
    if rule_type not in allowed_rules:
        raise ValueError("Unsupported alert rule")
    if channel not in {"local", "email", "browser", "whatsapp"}:
        raise ValueError("Unsupported alert channel")
    raw_config = payload.get("channel_config") if isinstance(payload.get("channel_config"), dict) else {}
    safe_config: dict[str, Any] = {"provider_connected": False}
    contact = str(raw_config.get("email") or raw_config.get("phone") or raw_config.get("endpoint") or "").strip()
    if contact:
        safe_config["contact_hash"] = hashlib.sha256(contact.encode("utf-8")).hexdigest()
        safe_config["contact_hint"] = contact[-4:]
    delivery_status = "local_only" if channel == "local" else "awaiting_provider"
    with transaction() as conn:
        row = conn.execute(
            """
            INSERT INTO alert_rules (
                variant_id, store_id, rule_type, threshold_amount, currency,
                channel, delivery_status, channel_config, consent_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
            RETURNING alert_id, variant_id, store_id, rule_type, threshold_amount,
                      currency, channel, delivery_status, created_at
            """,
            (
                variant_id,
                payload.get("store_id"),
                rule_type,
                payload.get("threshold_amount"),
                payload.get("currency") or "EGP",
                channel,
                delivery_status,
                __import__("json").dumps(safe_config),
            ),
        ).fetchone()
    result = _jsonable(dict(row))
    result["message"] = (
        "تم حفظ التنبيه على هذا الجهاز."
        if channel == "local"
        else "تم حفظ قاعدة التنبيه، لكن الإرسال ينتظر ربط مزود القناة والتحقق منها."
    )
    return result


def report_price_issue(payload: dict[str, Any], reporter_fingerprint: str | None = None) -> dict[str, Any]:
    report_type = str(payload.get("report_type") or "wrong_price")
    allowed = {"wrong_price", "wrong_variant", "wrong_availability", "broken_link", "shipping_mismatch", "coupon_invalid", "warranty_mismatch", "other"}
    if report_type not in allowed:
        raise ValueError("Unsupported report type")
    evidence = str(payload.get("evidence_url") or "").strip() or None
    if evidence and not evidence.startswith("https://"):
        raise ValueError("Evidence URL must use HTTPS")
    with transaction() as conn:
        row = conn.execute(
            """
            INSERT INTO price_reports (
                offer_id, plan_id, variant_id, store_id, report_type,
                description, reporter_fingerprint, evidence_url
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING report_id, status, created_at
            """,
            (
                payload.get("offer_id"),
                payload.get("plan_id"),
                payload.get("variant_id"),
                payload.get("store_id"),
                report_type,
                str(payload.get("description") or "")[:1000] or None,
                reporter_fingerprint,
                evidence,
            ),
        ).fetchone()
        entity_id = str(payload.get("offer_id") or payload.get("plan_id"))
        conn.execute(
            """
            INSERT INTO review_cases (
                entity_type, entity_id, issue_code, severity, title, description, payload
            ) VALUES ('price_report', %s, %s, 'high', 'بلاغ عن عرض يحتاج مراجعة', %s, %s::jsonb)
            ON CONFLICT DO NOTHING
            """,
            (
                entity_id,
                report_type,
                str(payload.get("description") or "")[:1000] or None,
                __import__("json").dumps(payload, ensure_ascii=False),
            ),
        )
    return _jsonable(dict(row))


def create_comparison_share(variant_ids: list[str], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = compare_products(variant_ids)
    with transaction() as conn:
        row = conn.execute(
            """
            INSERT INTO comparison_shares (variant_ids, settings, snapshot)
            VALUES (%s, %s::jsonb, %s::jsonb)
            RETURNING share_id, expires_at, created_at
            """,
            (
                list(dict.fromkeys(variant_ids)),
                __import__("json").dumps(settings or {}, ensure_ascii=False),
                __import__("json").dumps(snapshot, ensure_ascii=False),
            ),
        ).fetchone()
    return _jsonable({**dict(row), "snapshot": snapshot})
