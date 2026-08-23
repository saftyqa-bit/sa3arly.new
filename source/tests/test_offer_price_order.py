from pathlib import Path

from app.firestore_repository import FirestoreRepository

POSTGRES_REPOSITORY = Path("app/repository.py")


def test_postgres_comparison_falls_back_to_cash_price_when_shipping_is_unknown() -> None:
    source = POSTGRES_REPOSITORY.read_text(encoding="utf-8")
    comparison_query = source.split("FROM public_cash_offers", maxsplit=1)[1].split(
        '"""', maxsplit=1
    )[0]

    assert "COALESCE(comparable_total, cash_price) NULLS LAST" in comparison_query
    assert comparison_query.index("COALESCE(comparable_total, cash_price)") < (
        comparison_query.index("CASE computed_freshness")
    )


def test_firestore_comparison_orders_unknown_shipping_by_cash_price() -> None:
    rows = [
        {
            "store_name": "Ehab Center",
            "cash_price": 17_999,
            "comparable_total": None,
            "eligible_for_ranking": True,
            "computed_freshness": "fresh",
            "availability": "available",
        },
        {
            "store_name": "Vodafone Egypt E-Shop",
            "cash_price": 15_999,
            "comparable_total": None,
            "eligible_for_ranking": True,
            "computed_freshness": "fresh",
            "availability": "available",
        },
        {
            "store_name": "B.TECH",
            "cash_price": 15_897,
            "comparable_total": None,
            "eligible_for_ranking": True,
            "computed_freshness": "fresh",
            "availability": "available",
        },
    ]

    ordered = sorted(rows, key=FirestoreRepository._cash_sort_key)

    assert [row["cash_price"] for row in ordered] == [15_897, 15_999, 17_999]
