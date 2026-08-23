from __future__ import annotations

from datetime import UTC, datetime

from app.schemas import CatalogDiscoveryTaskPayload, FetchResult
from app.scraping.catalog_discovery import (
    CatalogDiscoveryEngine,
    _candidate_dict,
    _internal_error_code,
    _retryable_internal_error,
    canonical_catalog_url,
    extract_robots_sitemaps,
    likely_product_url,
    parse_sitemap,
    title_from_url,
)
from app.scraping.fetcher import FetchError
from app.scraping.types import ProductCandidate


def test_canonical_catalog_url_removes_tracking_but_keeps_variant_query():
    value = canonical_catalog_url(
        "https://www.example.com/product/phone/?utm_source=x&color=blue&gclid=1"
    )
    assert value == "https://www.example.com/product/phone?color=blue"


def test_product_url_filter_rejects_assets_and_account_pages():
    assert likely_product_url("https://shop.example/products/iphone-17-pro-256gb")
    assert likely_product_url(
        "https://shop.example/iphone-17-pro-256gb-a3520",
        product_sitemap=True,
    )
    assert not likely_product_url("https://shop.example/account/login")
    assert not likely_product_url("https://shop.example/media/phone.jpg")


def test_title_from_product_slug():
    assert title_from_url("https://shop.example/p/samsung-galaxy-s26-ultra.html") == (
        "samsung galaxy s26 ultra"
    )


def test_catalog_candidate_normalizes_non_string_brand_and_sku():
    candidate = ProductCandidate(
        title="Example product 12345",
        url="https://shop.example/products/example-12345",
        brand=123,  # type: ignore[arg-type]
        sku={"value": "SKU-123"},  # type: ignore[arg-type]
    )

    item = _candidate_dict(
        candidate,
        allowed_hosts=["shop.example"],
        final_url="https://shop.example/",
    )

    assert item is not None
    assert item["brand"] == "123"
    assert item["sku"] == "{'value': 'SKU-123'}"


def test_catalog_typeerror_code_keeps_a_safe_signature():
    error = TypeError("'int' object is not subscriptable")
    assert _internal_error_code(error) == "internal_typeerror_not_subscriptable"


def test_transient_database_pressure_is_retryable_but_code_defects_are_not():
    assert _retryable_internal_error("internal_pooltimeout")
    assert _retryable_internal_error("internal_deadlockdetected")
    assert not _retryable_internal_error("internal_typeerror_not_subscriptable")


def test_robots_sitemaps_are_deduplicated():
    body = """
    User-agent: *
    Sitemap: https://shop.example/sitemap.xml
    Sitemap: /product-sitemap.xml
    Sitemap: https://shop.example/sitemap.xml
    """
    assert extract_robots_sitemaps(body, "https://shop.example/en") == [
        "https://shop.example/sitemap.xml",
        "https://shop.example/product-sitemap.xml",
    ]


def test_sitemap_index_and_product_sitemap_parsing():
    index = """<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://shop.example/product-sitemap.xml</loc></sitemap>
      <sitemap><loc>https://shop.example/category-sitemap.xml</loc></sitemap>
    </sitemapindex>"""
    child, candidates = parse_sitemap(index, "https://shop.example/sitemap.xml")
    assert candidates == []
    assert child == [
        "https://shop.example/product-sitemap.xml",
        "https://shop.example/category-sitemap.xml",
    ]

    products = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://shop.example/en/apple-iphone-17-pro-256gb</loc></url>
      <url><loc>https://shop.example/account/login</loc></url>
      <url><loc>https://shop.example/image.jpg</loc></url>
    </urlset>"""
    child, candidates = parse_sitemap(
        products,
        "https://shop.example/product-sitemap.xml",
    )
    assert child == []
    assert [candidate.url for candidate in candidates] == [
        "https://shop.example/en/apple-iphone-17-pro-256gb"
    ]

def test_discovery_falls_back_to_product_sitemap_after_landing_timeout(monkeypatch):
    engine = CatalogDiscoveryEngine()
    payload = CatalogDiscoveryTaskPayload(
        task_id="CAT-fallback",
        run_id="00000000-0000-0000-0000-000000000010",
        run_slot=datetime(2026, 8, 5, 18, 0, tzinfo=UTC),
        scheduled_for=datetime(2026, 8, 5, 18, 0, tzinfo=UTC),
        source_id="CDS-EG-TEST",
        store_id="EG-TEST",
        store_name="Example Shop",
        source_url="https://shop.example/en",
        allowed_hosts=["shop.example"],
    )

    def fake_fetch(_payload, url, *, respect_robots, allow_browser):
        if url == payload.source_url:
            raise FetchError("landing page timed out", code="timeout")
        if url.endswith("/robots.txt"):
            return FetchResult(
                url=url,
                final_url=url,
                status_code=200,
                content_type="text/plain",
                body="Sitemap: https://shop.example/product-sitemap.xml",
            )
        if url.endswith("/product-sitemap.xml"):
            return FetchResult(
                url=url,
                final_url=url,
                status_code=200,
                content_type="application/xml",
                body="""<?xml version="1.0"?>
                <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
                  <url><loc>https://shop.example/products/phone-12345</loc></url>
                </urlset>""",
            )
        return FetchResult(
            url=url,
            final_url=url,
            status_code=404,
            content_type="text/plain",
            body="",
        )

    monkeypatch.setattr(engine, "_fetch_source", fake_fetch)

    candidates, metrics = engine._collect(payload)

    assert [item["normalized_url"] for item in candidates] == [
        "https://shop.example/products/phone-12345"
    ]
    assert metrics["source_failures"] == {"timeout": 1}


def test_discovery_hydrates_a_new_sitemap_url_with_product_price(monkeypatch):
    engine = CatalogDiscoveryEngine()
    monkeypatch.setattr(
        engine.settings,
        "catalog_discovery_max_product_fetches_per_store",
        1,
    )
    payload = CatalogDiscoveryTaskPayload(
        task_id="CAT-hydrate",
        run_id="00000000-0000-0000-0000-000000000011",
        run_slot=datetime(2026, 8, 9, 8, 0, tzinfo=UTC),
        scheduled_for=datetime(2026, 8, 9, 8, 0, tzinfo=UTC),
        source_id="CDS-EG-TEST",
        store_id="EG-TEST",
        store_name="Example Shop",
        source_url="https://shop.example/",
        allowed_hosts=["shop.example"],
    )
    url = "https://shop.example/products/phone-256gb"
    sitemap_item = {
        "source_url": url,
        "normalized_url": url,
        "title": "phone 256gb",
        "source_method": "sitemap_product_url",
    }
    monkeypatch.setattr(
        "app.scraping.catalog_discovery.repository.select_catalog_hydration_candidates",
        lambda store_id, candidates, *, limit: candidates[:limit],
    )

    def fake_fetch(_payload, requested_url, *, respect_robots, allow_browser):
        assert requested_url == url
        return FetchResult(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/html",
            response_bytes=500,
            body="""
            <script type="application/ld+json">
            {"@context":"https://schema.org","@type":"Product",
             "name":"Example Phone 256GB","brand":{"name":"Example"},
             "sku":"PHONE-256","offers":{"@type":"Offer",
             "price":"14999","priceCurrency":"EGP","availability":"InStock"}}
            </script>
            """,
        )

    monkeypatch.setattr(engine, "_fetch_source", fake_fetch)

    hydrated, metrics = engine._hydrate_product_candidates(payload, [sitemap_item])

    assert hydrated[0]["price"] == 14999
    assert hydrated[0]["normalized_url"] == url
    assert hydrated[0]["source_method"].startswith("jsonld")
    assert metrics["hydrated_candidates"] == 1

