from app.scraping.normalization import normalize_availability, parse_price


def test_parse_egyptian_arabic_price():
    assert parse_price("١٠٤٬٩٩٠ ج.م") == 104990.0
    assert parse_price("EGP 18,999") == 18999.0
    assert parse_price("1,234.50") == 1234.5


def test_availability():
    assert normalize_availability("متوفر الآن") == "available"
    assert normalize_availability("Out of stock") == "out_of_stock"
