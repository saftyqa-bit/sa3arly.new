from app.catalog_identity import (
    catalog_entity_identity,
    catalog_observation_publishable,
    catalog_technical_specs,
)


def test_gtin_identity_merges_the_same_product_across_stores() -> None:
    item = {"gtin": "6221234567890", "normalized_url": "https://a.example/p/1"}

    first = catalog_entity_identity(item, store_id="EG-001")
    second = catalog_entity_identity(item, store_id="EG-002")

    assert first == second
    assert first.identity_key == "gtin:6221234567890"
    assert first.strength == 100


def test_store_url_identity_never_false_merges_brandless_products() -> None:
    item = {"normalized_url": "https://shop.example/products/kettle"}

    first = catalog_entity_identity(item, store_id="EG-001")
    second = catalog_entity_identity(item, store_id="EG-002")

    assert first.entity_id != second.entity_id
    assert first.strength == 60


def test_valid_priced_import_can_be_visible_as_review_only_product() -> None:
    assert catalog_observation_publishable(
        {
            "normalized_url": "https://shop.example/products/phone",
            "title": "Example Phone 256GB",
            "price": 14999,
            "currency": "EGP",
            "availability": "in_stock",
            "evidence": {"price": True, "structured_signal_count": 4},
        },
        origin_type="catalog_import",
    )


def test_sitemap_only_candidate_is_not_published_without_product_evidence() -> None:
    assert not catalog_observation_publishable(
        {
            "normalized_url": "https://shop.example/products/phone",
            "title": "Example Phone",
            "price": 14999,
            "currency": "EGP",
            "source_method": "sitemap_product_url",
        },
        origin_type="catalog_discovery",
    )


def test_technical_specs_support_common_storage_ram_notation() -> None:
    assert catalog_technical_specs("iPhone 17 Pro 256/12") == {
        "ram_gb": 12,
        "storage_gb": 256,
    }
    assert catalog_technical_specs("Laptop 1TB 16GB RAM") == {
        "ram_gb": 16,
        "storage_gb": 1024,
    }
