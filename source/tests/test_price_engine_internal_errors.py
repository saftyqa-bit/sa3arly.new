from app.scraping.engine import (
    PARSED_PAGE_CACHE_SCHEMA_VERSION,
    _cache_payload_reusable,
    _cached_payload_has_price,
    _candidate_from_dict,
    _document_from_cache,
    _internal_error_code,
    _internal_failure_location,
)


def test_cached_candidate_normalizes_untrusted_field_types():
    candidate = _candidate_from_dict(
        {
            "title": {"unexpected": "mapping"},
            "url": ["https://example.com/product"],
            "price": {"amount": 100},
            "old_price": "250.5",
            "shipping_cost": float("nan"),
            "brand": {"name": "Example"},
            "raw": ["not", "a", "mapping"],
            "free_shipping": "yes",
        }
    )

    assert candidate.title == ""
    assert candidate.url is None
    assert candidate.price is None
    assert candidate.old_price == 250.5
    assert candidate.shipping_cost is None
    assert candidate.brand is None
    assert candidate.raw == {}
    assert candidate.free_shipping is None


def test_internal_error_codes_are_safe_and_typed():
    assert _internal_error_code(TypeError("private details")) == "internal_typeerror"
    assert _internal_error_code(AttributeError("private details")) == "internal_attributeerror"
    assert _internal_error_code(ValueError("private details")) == "internal_valueerror"
    assert _internal_error_code(KeyError("private details")) == "internal_keyerror"
    assert _internal_error_code(RuntimeError("private details")) == "internal_error"



def test_cached_document_normalizes_malformed_envelope():
    document = _document_from_cache(
        {
            "schema_version": PARSED_PAGE_CACHE_SCHEMA_VERSION,
            "final_url": {"unexpected": "mapping"},
            "title": ["unexpected", "list"],
            "visible_text": {"unexpected": "mapping"},
            "candidates": None,
            "links": ["not-a-candidate", {"title": "Valid cached item", "price": "99"}],
            "raw_summary": ["not", "a", "mapping"],
        }
    )

    assert document.final_url == ""
    assert document.title == ""
    assert document.visible_text == ""
    assert document.candidates == []
    assert len(document.links) == 1
    assert document.links[0].title == "Valid cached item"
    assert document.links[0].price == 99.0
    assert document.raw_summary == {}


def test_cache_envelope_checks_reject_wrong_containers_without_raising():
    assert _cache_payload_reusable(None) is False
    assert _cache_payload_reusable([]) is False
    assert _cached_payload_has_price({"candidates": None, "links": None}) is False
    assert _cached_payload_has_price(
        {"candidates": "not-a-list", "links": [{"price": 120}]}
    ) is True



def test_internal_failure_location_is_bounded_and_message_free():
    def raise_private_failure():
        raise TypeError("secret URL and payload must never be published")

    try:
        raise_private_failure()
    except TypeError as exc:
        location = _internal_failure_location(exc)

    assert location.startswith("dependency/test_price_engine_internal_errors.py:")
    assert ":raise_private_failure:" in location
    assert "secret" not in location
    assert "URL" not in location
