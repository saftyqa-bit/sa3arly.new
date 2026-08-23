from app.schemas import MappingTarget
from app.scraping.matching import score_candidate
from app.scraping.types import ProductCandidate


def target():
    return MappingTarget(
        mapping_id="MAP-1",
        offer_id="CASH-1",
        offer_key="VAR-1|EG-1|STORE",
        variant_id="VAR-1",
        store_id="EG-1",
        source_url="https://example.com",
        canonical_name="Apple iPhone 17 Pro Max 256GB Blue",
        brand="Apple",
        model="iPhone 17 Pro Max",
        variant_name="256GB Blue",
        storage_gb=256,
        color="Blue",
    )


def test_exact_variant_scores_high():
    candidate = ProductCandidate(
        title="Apple iPhone 17 Pro Max 256GB - Blue",
        price=104990,
        url="https://example.com/iphone",
    )
    assert score_candidate(target(), candidate) >= 55


def test_wrong_storage_is_penalized():
    correct = ProductCandidate(title="Apple iPhone 17 Pro Max 256GB Blue", price=1)
    wrong = ProductCandidate(title="Apple iPhone 17 Pro Max 128GB Blue", price=1)
    assert score_candidate(target(), correct) > score_candidate(target(), wrong) + 20
