from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app import decision_engine
from app.decision_engine import (
    cash_final_cost,
    enrich_cash_offers,
    enrich_installment_plans,
    get_purchase_decision,
    installment_final_cost,
    smart_search,
    summarize_price_history,
)
from app.decision_engine_runtime import normalize_arabic_search


def test_cash_final_cost_includes_all_known_components():
    assert cash_final_cost(
        {
            "cash_price": 25000,
            "shipping_cost": 120,
            "mandatory_fees": 30,
            "card_fees": 50,
            "coupon_discount": 300,
        }
    ) == 24900


def test_cash_final_cost_prefers_a_published_comparable_total():
    assert cash_final_cost(
        {
            "cash_price": 25000,
            "shipping_cost": None,
            "comparable_total": 25250,
        }
    ) == 25250


def test_installment_total_uses_down_payment_installments_and_fees():
    assert installment_final_cost(
        {
            "down_payment": 2000,
            "periodic_payment": 1000,
            "months": 12,
            "admin_fees": 400,
            "processing_fees": 100,
            "card_fees": 50,
            "shipping_cost": 100,
            "coupon_discount": 150,
        }
    ) == 14500


def test_published_installment_total_wins_when_present():
    assert installment_final_cost(
        {
            "total_published": 12345,
            "periodic_payment": 1,
            "months": 99,
        }
    ) == 12345


@pytest.mark.parametrize(
    ("query", "expected_tokens"),
    [
        ("ايفون ١٥ برو ماكس ٢٥٦ جيجا", ["iphone", "15", "pro max", "256", "gb"]),
        ("سامسونج-ايه-٥٥ 128GB", ["samsung", "55", "128", "gb"]),
        ("شاومي ريدمي نوت", ["xiaomi", "redmi", "note"]),
    ],
)
def test_arabic_search_normalization(query: str, expected_tokens: list[str]):
    normalized = normalize_arabic_search(query)
    for token in expected_tokens:
        assert token in normalized


def test_history_summary_exposes_decision_statistics():
    now = datetime.now(UTC)
    rows = [
        {
            "store_id": "store-a",
            "observed_at": now - timedelta(days=20),
            "observed_price": 27000,
            "snapshot": {},
        },
        {
            "store_id": "store-b",
            "observed_at": now - timedelta(days=10),
            "observed_price": 26000,
            "snapshot": {"coupon_code": "SAVE500"},
        },
        {
            "store_id": "store-a",
            "observed_at": now - timedelta(days=1),
            "observed_price": 25000,
            "snapshot": {},
        },
    ]
    summary = summarize_price_history(rows)
    assert summary["lowest_30d"] == 25000
    assert summary["lowest_90d"] == 25000
    assert summary["highest_90d"] == 27000
    assert summary["change_count"] == 2
    assert summary["last_drop_at"] is not None
    assert summary["trend"] == "declining"
    assert len(summary["sparkline"]) == 3
    assert summary["markers"][0]["label"] == "SAVE500"
    assert summary["store_count"] == 2
    assert summary["sufficient_for_recommendation"] is True


def test_decision_offer_ranking_uses_final_cost_and_explanation():
    history = {
        "average_90d": 27000,
        "lowest_90d": 24800,
        "sufficient_for_recommendation": True,
    }
    offers = enrich_cash_offers(
        [
            {
                "offer_id": "A",
                "store_id": "store-a",
                "store_name": "Store A",
                "cash_price": 25000,
                "shipping_cost": 200,
                "coupon_discount": 0,
                "availability": "available",
                "last_success_at": datetime.now(UTC),
                "match_quality_score": 95,
                "store_verified": True,
            },
            {
                "offer_id": "B",
                "store_id": "store-b",
                "store_name": "Store B",
                "cash_price": 25200,
                "shipping_cost": 0,
                "coupon_discount": 300,
                "availability": "available",
                "last_success_at": datetime.now(UTC),
                "match_quality_score": 95,
                "store_verified": True,
            },
        ],
        history,
    )
    assert offers[0]["offer_id"] == "B"
    assert offers[0]["final_cost"] == 24900
    assert "الأقل بعد الشحن" in offers[0]["explanation"]
    assert offers[0]["price_position"]["tone"] in {"excellent", "good"}


def test_impossible_cash_price_is_hidden_and_cannot_drive_a_recommendation():
    history = summarize_price_history(
        [{
            "store_id": "store-a",
            "observed_at": datetime.now(UTC),
            "observed_price": 2,
            "snapshot": {"title": "Bowl set of 2"},
        }]
    )
    assert history["observation_count"] == 0
    assert history["sufficient_for_recommendation"] is False
    assert enrich_cash_offers(
        [{"offer_id": "bad", "store_name": "Store", "cash_price": 2}],
        history,
    ) == []


def test_installment_enrichment_deduplicates_and_rejects_incomplete_plans():
    rows = [
        {
            "plan_id": "first",
            "store_id": "store-a",
            "months": 12,
            "periodic_payment": 1000,
            "total_published": 12000,
        },
        {
            "plan_id": "duplicate",
            "store_id": "store-a",
            "months": 12,
            "periodic_payment": 1000,
            "total_published": 12000,
        },
        {
            "plan_id": "incomplete",
            "store_id": "store-a",
            "periodic_payment": 900,
        },
    ]
    plans = enrich_installment_plans(rows)
    assert [plan["plan_id"] for plan in plans] == ["first"]


def test_review_price_is_visible_but_blocked_price_stays_hidden():
    history = {
        "average_90d": 20000,
        "lowest_90d": 19000,
        "sufficient_for_recommendation": False,
    }
    offers = enrich_cash_offers(
        [
            {
                "offer_id": "review",
                "store_name": "Review Store",
                "cash_price": 19500,
                "anomaly_status": "review",
            },
            {
                "offer_id": "blocked",
                "store_name": "Blocked Store",
                "cash_price": 100,
                "anomaly_status": "blocked",
            },
        ],
        history,
    )
    assert [offer["offer_id"] for offer in offers] == ["review"]
    assert offers[0]["verification_status"] == "needs_review"


def test_purchase_decision_keeps_public_prices_when_analysis_views_fail(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        decision_engine.repository,
        "get_product_comparison",
        lambda *args, **kwargs: {
            "product": {
                "variant_id": "VAR-ABC123",
                "canonical_name": "Test phone",
            },
            "cash_offers": [
                {
                    "offer_id": "offer-1",
                    "store_id": "store-1",
                    "store_name": "Store One",
                    "cash_price": 25000,
                    "comparable_total": 25200,
                    "shipping_cost_known": True,
                    "availability": "available",
                    "anomaly_status": "clear",
                }
            ],
            "installment_plans": [],
        },
    )
    for name in ("_load_history", "_cash_rows", "_installment_rows"):
        monkeypatch.setattr(
            decision_engine,
            name,
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("view down")),
        )
    monkeypatch.setattr(
        decision_engine,
        "smart_alternatives",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("view down")),
    )

    result = get_purchase_decision("VAR-ABC123")

    assert result is not None
    assert result["cash_offers"][0]["final_cost"] == 25200
    assert result["purchase_index"]["best_offer_id"] == "offer-1"
    assert result["degraded_components"] == [
        "history",
        "cash_analysis",
        "installment_analysis",
        "alternatives",
    ]


def test_smart_search_falls_back_to_basic_catalog_search(
    monkeypatch: pytest.MonkeyPatch,
):
    @contextmanager
    def unavailable_connection():
        raise RuntimeError("smart search view down")
        yield

    seen: dict[str, object] = {}

    def basic_search(query: str, *, limit: int):
        seen.update({"query": query, "limit": limit})
        return [
            {
                "variant_id": "VAR-ABC123",
                "canonical_name": "Samsung A55",
                "brand": "Samsung",
                "model": "A55",
                "relevance": 0.75,
            }
        ]

    monkeypatch.setattr(decision_engine, "connection", unavailable_connection)
    monkeypatch.setattr(decision_engine.repository, "search_products", basic_search)

    result = smart_search("سامسونج ايه ٥٥", limit=5)

    assert seen == {"query": "samsung a 55", "limit": 5}
    assert result["degraded"] is True
    assert result["items"][0]["search_score"] == 0.75
    assert result["suggestion"] == "Samsung A55"
