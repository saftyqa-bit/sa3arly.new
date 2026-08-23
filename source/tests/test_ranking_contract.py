from pathlib import Path

from app.schemas import MappingTarget
from app.scraping.matching import CONFLICT_SCORE, choose_best_candidate, score_candidate
from app.scraping.types import ProductCandidate


def target(**overrides):
    values = {
        "mapping_id": "MAP-1",
        "offer_id": "OFFER-1",
        "offer_key": "VAR-1|STORE-1|STORE",
        "variant_id": "VAR-1",
        "store_id": "STORE-1",
        "source_url": "https://shop.example/product",
        "canonical_name": "Samsung Galaxy S26 Ultra 256GB 12GB Black",
        "brand": "Samsung",
        "model": "Galaxy S26 Ultra",
        "storage_gb": 256,
        "ram_gb": 12,
        "color": "Black",
        "manufacturer_sku": "SM-S948B",
        "gtin": "1234567890123",
    }
    values.update(overrides)
    return MappingTarget(**values)


def candidate(title: str, **overrides):
    values = {
        "title": title,
        "url": "https://shop.example/product",
        "price": 50_000,
        "sku": "SM-S948B",
        "gtin": "1234567890123",
    }
    values.update(overrides)
    return ProductCandidate(**values)


def test_conflicting_decisive_identifiers_are_hard_rejected():
    assert score_candidate(target(), candidate("Samsung S26 Ultra 256GB 12GB Black", gtin="999")) == CONFLICT_SCORE
    assert (
        score_candidate(
            target(store_sku="STORE-S26-256-BLK"),
            candidate("Samsung S26 Ultra 256GB 12GB Black", sku="OTHER"),
        )
        == CONFLICT_SCORE
    )


def test_retailer_uuid_sku_does_not_conflict_with_manufacturer_model():
    btech = target(
        canonical_name="LG MH8295CIS 42 L 1200 W Silver 42L",
        brand="LG",
        model="MH8295CIS",
        storage_gb=None,
        ram_gb=None,
        color="Silver",
        manufacturer_sku="MH8295CIS",
        gtin=None,
        store_sku=None,
    )
    jsonld = candidate(
        "LG Microwave with Grill, 42 Liter 1200 Watt, Silver - MH8295CIS",
        sku="04a596d4-2846-42e9-a693-32fc5f32d631",
        gtin=None,
        price=11_450,
        url="https://btech.com/en/p/04a596d4-2846-42e9-a693-32fc5f32d631",
    )

    assert score_candidate(btech, jsonld) >= 40


def test_retailer_uuid_sku_does_not_override_wrong_model_rejection():
    btech = target(
        canonical_name="LG MH8295CIS 42 L 1200 W Silver 42L",
        brand="LG",
        model="MH8295CIS",
        storage_gb=None,
        ram_gb=None,
        color="Silver",
        manufacturer_sku="MH8295CIS",
        gtin=None,
        store_sku=None,
    )
    wrong_model = candidate(
        "LG Microwave with Grill, 42 Liter 1200 Watt, Silver - MH8295DIS",
        sku="another-retailer-uuid",
        gtin=None,
        price=11_450,
    )

    assert score_candidate(btech, wrong_model) == CONFLICT_SCORE


def test_conflicting_variant_specs_are_hard_rejected():
    assert score_candidate(target(), candidate("Samsung Galaxy S26 Ultra 512GB 12GB Black")) == CONFLICT_SCORE
    assert score_candidate(target(), candidate("Samsung Galaxy S26 Ultra 256GB 16GB Black")) == CONFLICT_SCORE
    assert score_candidate(target(), candidate("Samsung Galaxy S26 Ultra 256GB 12GB White")) == CONFLICT_SCORE


def test_ambiguous_near_tie_is_not_auto_published():
    first = candidate("Samsung Galaxy S26 Ultra 256GB 12GB Black")
    second = candidate(
        "Samsung Galaxy S26 Ultra 256GB 12GB Black Official",
        url="https://shop.example/product-2",
    )
    selected, score = choose_best_candidate(target(), [first, second])
    assert selected is None
    assert score > 0


def test_duplicate_same_offer_evidence_is_not_treated_as_ambiguity():
    first = candidate("Samsung Galaxy S26 Ultra 256GB 12GB Black")
    second = candidate(
        "Samsung Galaxy S26 Ultra 256GB 12GB Black | B.TECH",
        source_method="jsonld",
    )
    selected, score = choose_best_candidate(target(), [first, second])
    assert selected is first
    assert score > 0


def test_same_page_meta_without_price_does_not_hide_structured_offer():
    btech = target(
        mapping_id="MAP-ED6D86D77EA39558",
        offer_id="CASH-A9B3EFD32607C0",
        offer_key="VAR-44F543D7D0E5E3|EG-013|STORE",
        variant_id="VAR-44F543D7D0E5E3",
        store_id="EG-013",
        source_url="https://btech.com/en/p/6b37bfb8-0c91-426a-99e3-00bec04e874e",
        canonical_name="Ultra UT40SEL2 40 بوصة / HD LED / Android 14",
        brand="Ultra",
        model="UT40SEL2",
        storage_gb=None,
        ram_gb=None,
        color="",
        manufacturer_sku="UT40SEL2",
        gtin="",
        store_sku="",
    )
    title = (
        "Ultra 40 Inch Smart TV , HD LED Built-In Receiver - UT40SEL2, "
        "With TV Wall Mount"
    )
    url = btech.source_url
    jsonld_offer = candidate(
        title,
        url=url,
        price=9508.0,
        currency="EGP",
        availability="out_of_stock",
        sku="6b37bfb8-0c91-426a-99e3-00bec04e874e",
        gtin=None,
        brand="Ultra",
        source_method="jsonld_offer",
    )
    html_meta = candidate(
        title,
        url=url,
        price=None,
        currency="EGP",
        availability=None,
        sku=None,
        gtin=None,
        brand=None,
        source_method="html_meta",
    )

    selected, score = choose_best_candidate(btech, [jsonld_offer, html_meta])

    assert score == 75.953
    assert selected is jsonld_offer


def test_same_page_with_conflicting_prices_remains_ambiguous():
    first = candidate("Samsung Galaxy S26 Ultra 256GB 12GB Black", price=50_000)
    second = candidate(
        "Samsung Galaxy S26 Ultra 256GB 12GB Black | B.TECH",
        price=45_000,
        source_method="jsonld",
    )
    selected, score = choose_best_candidate(target(), [first, second])
    assert selected is None
    assert score > 0


def test_views_never_treat_unknown_shipping_as_zero_and_gate_partial_plans():
    sql = (
        Path(__file__).resolve().parents[1]
        / "db"
        / "migrations"
        / "001_sa3arly_core_v2.sql"
    ).read_text(encoding="utf-8")
    public_cash = sql.split(
        "CREATE OR REPLACE VIEW pricing.public_cash_offers", 1
    )[1].split("CREATE OR REPLACE VIEW", 1)[0]
    assert "cash_price + COALESCE(o.shipping_cost, 0)" not in public_cash
    assert "i.completeness = 'complete'" in sql
    assert "i.starting_from_only = FALSE" in sql
