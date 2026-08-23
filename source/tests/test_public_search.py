from app.repository import normalize_public_search_query


def test_arabic_brand_and_model_aliases_are_searchable():
    assert normalize_public_search_query("ايفون 17 برو ماكس") == "iphone 17 pro max"
    assert normalize_public_search_query("سامسونج S26 الترا") == "samsung s26 ultra"


def test_english_query_is_kept_normalized():
    assert normalize_public_search_query("  iPhone   17 PRO  ") == "iphone 17 pro"
