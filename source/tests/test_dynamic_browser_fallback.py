from __future__ import annotations

import pytest

from app.schemas import FetchResult
from app.scraping.engine import (
    PARSED_PAGE_CACHE_SCHEMA_VERSION,
    ScrapeEngine,
)
from app.settings import Settings


class _Repository:
    def __init__(self):
        self.cached_document = None

    def reserve_store_request_slot(self, *args, **kwargs):
        return 0

    def get_page_cache(self, *args, **kwargs):
        return None

    def upsert_page_cache(self, *args, **kwargs):
        self.cached_document = kwargs["parsed_payload"]


class _Fetcher:
    def __init__(self):
        self.browser_calls = 0

    def fetch(self, url, **kwargs):
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            body="<html><head><title>Unionaire INV-ARTO012</title></head><body>Price drop</body></html>",
            response_bytes=95,
            used_browser=False,
        )

    def fetch_browser(self, url, **kwargs):
        self.browser_calls += 1
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/html; rendered=playwright",
            body="""
            <html><head><title>Unionaire INV-ARTO012</title></head><body><main>
              <h1>Unionaire Artify Inverter Split Air Conditioner - INV-ARTO012</h1>
              <div>EGP 18,380 Lowest in 30 days</div>
              <div>From 1,268.62/mo with mylo</div>
            </main></body></html>
            """,
            response_bytes=310,
            used_browser=True,
        )


class _StaleCacheRepository(_Repository):
    def __init__(self):
        super().__init__()
        self.cache = {
            "etag": '"q11i-old"',
            "last_modified": "Fri, 01 Aug 2026 12:00:00 GMT",
            "parsed_payload": {
                "final_url": "https://btech.com/en/p/q11i",
                "title": "Anker Soundcore Q11i - A3005",
                "visible_text": "A3005 EGP",
                "candidates": [
                    {
                        "title": "Anker Soundcore Q11i - A3005",
                        "url": "https://btech.com/en/p/q11i",
                        "price": 3005,
                        "currency": "EGP",
                        "source_method": "html_visible_direct",
                    }
                ],
                "links": [],
                "raw_summary": {},
            },
        }

    def get_page_cache(self, *args, **kwargs):
        return self.cache


class _NotModifiedThenFreshQ11iFetcher:
    def __init__(self):
        self.cache_headers_seen = []

    def fetch(self, url, **kwargs):
        self.cache_headers_seen.append(kwargs.get("cache_headers"))
        if len(self.cache_headers_seen) == 1:
            return FetchResult(
                url=url,
                final_url=url,
                status_code=304,
                not_modified=True,
            )
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            body="""
            <html><head>
              <title>Anker Soundcore Q11i Wireless Headphones, Black - A3005</title>
              <script type="application/ld+json">{
                "@type": "Product",
                "name": "Anker Soundcore Q11i Over Ear Headphones, Black - A3005",
                "sku": "anker-soundcore-q11i-wireless-headphones-black-a3005",
                "offers": {"@type": "Offer", "price": 4176,
                           "priceCurrency": "EGP", "availability": "InStock"}
              }</script>
            </head><body>
              <h1>Anker Soundcore Q11i Wireless Headphones, Black - A3005</h1>
              <div>A3005 EGP</div>
            </body></html>
            """,
            response_bytes=700,
            used_browser=False,
        )

    def fetch_browser(self, *args, **kwargs):
        raise AssertionError("Browser fallback must not run for the structured Q11i price")


def test_load_document_retries_unpriced_http_with_browser(
    monkeypatch: pytest.MonkeyPatch,
):
    repository = _Repository()
    monkeypatch.setattr("app.scraping.engine.repository", repository)
    engine = ScrapeEngine()
    engine.settings = Settings(_env_file=None, enable_browser_fallback=True)
    engine.fetcher = _Fetcher()
    engine._connector_config = {}

    document, status, response_bytes, used_browser = engine._load_document(
        store_id="EG-013",
        source_url="https://btech.com/en/p/example",
        allowed_hosts=["btech.com"],
        respect_robots=True,
        browser_required=False,
        requests_per_minute=12,
    )

    assert status == 200
    assert response_bytes == 405
    assert used_browser is True
    assert engine.fetcher.browser_calls == 1
    assert any(candidate.price == 18_380 for candidate in document.candidates)
    assert any(
        candidate["price"] == 18_380
        for candidate in repository.cached_document["candidates"]
    )


def test_http_304_with_pre_q11i_cache_forces_full_reparse(
    monkeypatch: pytest.MonkeyPatch,
):
    repository = _StaleCacheRepository()
    monkeypatch.setattr("app.scraping.engine.repository", repository)
    engine = ScrapeEngine()
    engine.settings = Settings(_env_file=None, enable_browser_fallback=True)
    engine.fetcher = _NotModifiedThenFreshQ11iFetcher()
    engine._connector_config = {}

    document, status, response_bytes, used_browser = engine._load_document(
        store_id="EG-013",
        source_url="https://btech.com/en/p/q11i",
        allowed_hosts=["btech.com"],
        respect_robots=True,
        browser_required=False,
        requests_per_minute=12,
    )

    assert status == 200
    assert response_bytes == 700
    assert used_browser is False
    assert engine.fetcher.cache_headers_seen[0].etag == '"q11i-old"'
    assert engine.fetcher.cache_headers_seen[1] is None
    priced = [candidate for candidate in document.candidates if candidate.price is not None]
    assert len(priced) == 1
    assert priced[0].price == 4176
    assert priced[0].source_method == "jsonld_offer"
    assert not any(
        candidate.price == 3005 or candidate.source_method == "html_visible_direct"
        for candidate in document.candidates
    )
    assert (
        repository.cached_document["schema_version"]
        == PARSED_PAGE_CACHE_SCHEMA_VERSION
    )
