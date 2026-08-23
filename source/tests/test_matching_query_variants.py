from app.schemas import MappingTarget
from app.scraping.matching import choose_best_candidate
from app.scraping.types import ProductCandidate


def test_query_variant_and_plain_url_are_one_offer():
    target = MappingTarget(
        mapping_id="MAP-1",
        offer_id="CASH-1",
        offer_key="VAR-1|EG-1|STORE",
        variant_id="VAR-1",
        store_id="EG-1",
        source_url="https://example.com/products/1-24-gma-t-50",
        canonical_name="1/24 GMA T.50",
        brand="",
        model="GMA T.50",
    )
    candidates = [
        ProductCandidate(
            title="1/24 GMA T.50",
            price=4550.0,
            url="https://example.com/products/1-24-gma-t-50?variant=41923228991582",
            source_method="jsonld_offer",
        ),
        ProductCandidate(
            title="1/24 GMA T.50",
            price=4550.0,
            url="https://example.com/products/1-24-gma-t-50",
            source_method="html_visible_direct",
        ),
    ]
    best, score = choose_best_candidate(target, candidates)
    assert best is not None
    assert score > 0


def test_query_variants_with_conflicting_prices_remain_ambiguous():
    target = MappingTarget(
        mapping_id="MAP-2",
        offer_id="CASH-2",
        offer_key="VAR-2|EG-1|STORE",
        variant_id="VAR-2",
        store_id="EG-1",
        source_url="https://example.com/products/item",
        canonical_name="Example Model",
        brand="Example",
        model="Model",
    )
    candidates = [
        ProductCandidate(
            title="Example Model",
            price=1000.0,
            url="https://example.com/products/item?variant=1",
        ),
        ProductCandidate(
            title="Example Model",
            price=1500.0,
            url="https://example.com/products/item",
        ),
    ]
    best, _ = choose_best_candidate(target, candidates)
    assert best is None
