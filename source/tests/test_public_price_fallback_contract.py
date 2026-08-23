from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from app import priced_products


def test_priced_directory_uses_the_stable_public_price_views():
    source = Path("app/priced_products.py").read_text(encoding="utf-8")

    assert "FROM public_cash_offers" in source
    assert "FROM public_installment_offers" in source
    assert "MIN(COALESCE(comparable_total, cash_price)) FILTER" in source
    assert "MIN(comparable_total) FILTER" in source


def test_priced_directory_keeps_review_prices_visible_but_out_of_rankings():
    source = Path("app/priced_products.py").read_text(encoding="utf-8")

    assert source.count("FROM public_cash_offers") == 1
    assert source.count("WHERE cash_price >= 10") == 1
    assert source.count("COUNT(*) FILTER (\n                       WHERE eligible_for_ranking") >= 2
    assert source.count("COUNT(*) FILTER (\n                       WHERE NOT eligible_for_ranking") >= 2
    assert "AS review_cash_offer_count" in source
    assert "AS cash_price_review_required" in source
    assert "confirmed_cash_offer_count DESC" in source


def test_priced_directory_does_not_couple_cash_and_installment_views(monkeypatch):
    class Result:
        def __init__(self, row=None, rows=None):
            self.row = row
            self.rows = rows

        def fetchone(self):
            return self.row

        def fetchall(self):
            return self.rows

    class FakeConnection:
        def __init__(self):
            self.queries = []

        def execute(self, sql, _params):
            self.queries.append(sql)
            if "SELECT COUNT(*) AS total" in sql:
                return Result(row={"total": 0})
            return Result(rows=[])

    fake = FakeConnection()

    @contextmanager
    def fake_connection():
        yield fake

    monkeypatch.setattr(priced_products, "connection", fake_connection)
    monkeypatch.setattr(
        priced_products,
        "get_settings",
        lambda: SimpleNamespace(persistence_backend="postgres"),
    )

    priced_products.list_priced_products(mode="cash")
    assert all("public_installment_offers" not in sql for sql in fake.queries)
    assert all("public_cash_offers" in sql for sql in fake.queries)

    fake.queries.clear()
    priced_products.list_priced_products(mode="installment")
    assert all("public_cash_offers" not in sql for sql in fake.queries)
    assert all("public_installment_offers" in sql for sql in fake.queries)


def test_priced_directory_casts_nullable_postgres_parameters():
    source = Path("app/priced_products.py").read_text(encoding="utf-8")

    assert source.count("CAST(%s AS TEXT) IS NULL") == 4
    assert "AND (%s IS NULL OR" not in source


def test_frontend_falls_back_from_decision_analysis_to_basic_comparison():
    source = Path("sites-frontend-v13/app/decision-panel.tsx").read_text(
        encoding="utf-8"
    )

    assert "/decision`" in source
    assert "/comparison`" in source
    assert "comparisonFallback" in source
    assert "تكلفة الشحن غير معروفة بعد" in source


def test_live_search_proxy_has_a_basic_search_fallback():
    source = Path("sites-frontend-v13/app/api/live/search/route.ts").read_text(
        encoding="utf-8"
    )

    assert "/products/search/smart" in source
    assert "/products/search?" in source
