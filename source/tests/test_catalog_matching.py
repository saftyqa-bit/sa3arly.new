from __future__ import annotations

from app.catalog_matching import (
    build_catalog_variant_index,
    catalog_candidate_has_match_evidence,
    deterministic_catalog_match,
)


def _row(variant_id: str, **values):
    return {
        "variant_id": variant_id,
        "canonical_name": values.get("canonical_name", "Samsung Galaxy S26 Ultra 256GB Black"),
        "brand": values.get("brand", "Samsung"),
        "model": values.get("model", "S26 Ultra"),
        "variant_name": values.get("variant_name"),
        "manufacturer_sku": values.get("manufacturer_sku", "SM-S948BZK"),
        "gtin": values.get("gtin"),
        "storage_gb": values.get("storage_gb", 256),
        "ram_gb": values.get("ram_gb"),
        "color": values.get("color", "Black"),
        "source_status": "seed_verified",
        "active": True,
    }


def test_sitemap_url_matches_unique_brand_and_model_without_lowering_fuzzy_threshold():
    index = build_catalog_variant_index([_row("VAR-1")])
    best, score, method = deterministic_catalog_match(
        index,
        {
            "title": "Samsung Galaxy S26 Ultra 256 GB Black",
            "normalized_url": "https://shop.example/products/samsung-galaxy-s26-ultra-256-black",
        },
        store_id="EG-001",
    )
    assert best["variant_id"] == "VAR-1"
    assert score == 150
    assert method == "catalog_unique_brand_model"


def test_manufacturer_sku_in_product_slug_is_deterministic():
    index = build_catalog_variant_index([_row("VAR-1")])
    best, score, method = deterministic_catalog_match(
        index,
        {
            "title": "Samsung flagship phone",
            "normalized_url": "https://shop.example/p/samsung-sm-s948bzk",
        },
        store_id="EG-001",
    )
    assert best["variant_id"] == "VAR-1"
    assert score == 175
    assert method == "catalog_url_manufacturer_sku"


def test_same_model_with_multiple_variants_stays_in_review():
    index = build_catalog_variant_index(
        [
            _row("VAR-BLACK", color="Black"),
            _row("VAR-SILVER", canonical_name="Samsung Galaxy S26 Ultra 256GB Silver", color="Silver"),
        ]
    )
    best, score, method = deterministic_catalog_match(
        index,
        {
            "title": "Samsung Galaxy S26 Ultra",
            "normalized_url": "https://shop.example/products/samsung-galaxy-s26-ultra",
        },
        store_id="EG-001",
    )
    assert best is None
    assert score < 0
    assert method is None


def test_conflicting_variant_colour_is_never_auto_matched():
    index = build_catalog_variant_index([_row("VAR-BLACK", color="Black")])
    best, score, method = deterministic_catalog_match(
        index,
        {
            "title": "Samsung Galaxy S26 Ultra 256GB Silver",
            "normalized_url": "https://shop.example/products/samsung-s26-ultra-256-silver",
        },
        store_id="EG-001",
    )
    assert best is None
    assert score < 0
    assert method is None

def test_brandless_distinctive_model_can_match_safely():
    index = build_catalog_variant_index(
        [
            _row(
                "VAR-IPHONE",
                canonical_name="Apple iPhone 15 Pro 256GB Black",
                brand="Apple",
                model="iPhone 15 Pro",
                manufacturer_sku="MTV13",
            )
        ]
    )
    best, score, method = deterministic_catalog_match(
        index,
        {
            "title": "iPhone 15 Pro 256GB Black",
            "normalized_url": "https://shop.example/products/iphone-15-pro-256-black",
        },
        store_id="EG-001",
    )
    assert best["variant_id"] == "VAR-IPHONE"
    assert score == 150
    assert method == "catalog_unique_brand_model"


def test_unknown_product_does_not_justify_fuzzy_database_lookup():
    index = build_catalog_variant_index([_row("VAR-1")])
    assert (
        catalog_candidate_has_match_evidence(
            index,
            {
                "title": "Generic electric kettle",
                "normalized_url": "https://shop.example/products/generic-kettle",
            },
        )
        is False
    )


def test_known_model_anchor_justifies_fuzzy_database_lookup():
    index = build_catalog_variant_index(
        [_row("VAR-IPHONE", brand="Apple", model="iPhone 15 Pro")]
    )
    assert catalog_candidate_has_match_evidence(
        index,
        {
            "title": "iPhone 15 Pro",
            "normalized_url": "https://shop.example/products/iphone-15-pro",
        },
    )

