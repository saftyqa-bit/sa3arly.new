from __future__ import annotations

from app.catalog_bootstrap import (
    canonical_product_url,
    normalize_external_product_record,
)
from app.repository import _bootstrap_match_item


def test_uws_product_record_is_normalized_with_safe_identifier_separation() -> None:
    record = {
        "URL": "https://btech.com/ar/p/samsung-galaxy-a55?utm_source=test&color=blue",
        "name": "Samsung Galaxy A55 256GB 8GB Blue",
        "brand": {"name": "Samsung"},
        "sku": "BTECH-998877",
        "mpn": "SM-A556E",
        "gtin13": "8806095461234",
        "offers": {
            "price": "14,999 EGP",
            "priceCurrency": "EGP",
            "availability": "https://schema.org/InStock",
        },
        "image": ["https://cdn.example/a55.webp"],
    }

    result = normalize_external_product_record(record, allowed_hosts=["btech.com"])

    assert result.validation_status == "accepted"
    assert result.normalized_url == "https://btech.com/ar/p/samsung-galaxy-a55?color=blue"
    assert result.brand == "Samsung"
    assert result.merchant_sku == "BTECH-998877"
    assert result.manufacturer_sku == "SM-A556E"
    assert result.gtin == "8806095461234"
    assert result.price == 14999
    assert result.currency == "EGP"
    assert result.availability == "in_stock"

    match_item = _bootstrap_match_item(result.as_dict())
    assert match_item["sku"] == "SM-A556E"
    assert "BTECH-998877" not in match_item.values()


def test_uws_shopify_variant_columns_are_normalized() -> None:
    result = normalize_external_product_record(
        {
            "Product URL": "https://shop.example/products/anta-heritage",
            "Title": "Anta Heritage Beige Green",
            "Vendor": "ANTA",
            "Variant SKU": "812428875-3-7.5",
            "Variant Barcode": "1234567890123",
            "Variant Price": "2,900.00",
            "Available": "Yes",
            "Currency": "EGP",
            "Image Src": "https://cdn.example/anta.jpg",
            "Type": "FOOTWEAR",
        },
        allowed_hosts=["shop.example"],
    )

    assert result.validation_status == "accepted"
    assert result.price == 2900
    assert result.currency == "EGP"
    assert result.availability == "in_stock"
    assert result.image_url == "https://cdn.example/anta.jpg"
    assert result.gtin == "1234567890123"
    assert result.merchant_sku == "812428875-3-7.5"
    assert result.manufacturer_sku is None


def test_category_link_is_rejected_even_when_it_has_a_price_card() -> None:
    result = normalize_external_product_record(
        {
            "url": "https://btech.com/ar/c/mobiles-tablets/mobile-phones",
            "title": "Mobile phones",
            "price": "10000 EGP",
            "brand": "Samsung",
        },
        allowed_hosts=["btech.com"],
    )

    assert result.validation_status == "rejected"
    assert result.rejection_code == "listing_url"


def test_foreign_host_and_empty_rows_are_rejected() -> None:
    foreign = normalize_external_product_record(
        {
            "url": "https://attacker.example/products/phone",
            "title": "Samsung phone",
            "price": 999,
        },
        allowed_hosts=["btech.com"],
    )
    empty = normalize_external_product_record({}, allowed_hosts=["btech.com"])

    assert foreign.rejection_code == "host_not_allowed"
    assert empty.rejection_code == "missing_url"


def test_uws_organization_fallback_is_not_treated_as_a_product() -> None:
    result = normalize_external_product_record(
        {
            "PAGE URL": "https://btech.com/ar/p/missing-product",
            "Name": "B.Tech",
            "Type": "Organization",
            "Image": "https://btech.com/opengraph-image.png",
        },
        allowed_hosts=["btech.com"],
    )

    assert result.validation_status == "rejected"
    assert result.rejection_code == "structured_type_not_product"


def test_generic_descriptive_product_url_needs_two_structured_signals() -> None:
    accepted = normalize_external_product_record(
        {
            "url": "https://shop.example/en/samsung-galaxy-a55-256gb-blue",
            "title": "Samsung Galaxy A55 256GB Blue",
            "brand": "Samsung",
            "price": 15000,
        },
        allowed_hosts=["shop.example"],
    )
    rejected = normalize_external_product_record(
        {
            "url": "https://shop.example/en/samsung-galaxy-a55-256gb-blue",
            "title": "Samsung Galaxy A55 256GB Blue",
            "brand": "Samsung",
        },
        allowed_hosts=["shop.example"],
    )

    assert accepted.validation_status == "accepted"
    assert rejected.rejection_code == "ambiguous_page_type"


def test_canonical_product_url_removes_tracking_without_losing_variant() -> None:
    assert (
        canonical_product_url("HTTPS://WWW.Example.COM/products/phone/?utm_campaign=x&variant=blue&gclid=1")
        == "https://www.example.com/products/phone?variant=blue"
    )
