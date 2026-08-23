from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

from app import repository


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


def test_new_catalog_urls_are_hydrated_before_the_old_backlog(monkeypatch) -> None:
    old_url = "https://shop.example/products/old"
    new_url = "https://shop.example/products/new"

    class FakeConnection:
        @staticmethod
        def execute(sql, params):
            return _Rows(
                [
                    {
                        "normalized_url": old_url,
                        "observed_price": None,
                        "source_method": "sitemap_product_url",
                        "last_seen_at": datetime(2026, 8, 1, tzinfo=UTC),
                    }
                ]
            )

    @contextmanager
    def fake_connection():
        yield FakeConnection()

    monkeypatch.setattr(repository, "connection", fake_connection)

    selected = repository.select_catalog_hydration_candidates(
        "EG-TEST",
        [
            {"normalized_url": old_url},
            {"normalized_url": new_url},
        ],
        limit=1,
    )

    assert selected == [{"normalized_url": new_url}]
