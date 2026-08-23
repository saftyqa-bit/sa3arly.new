from __future__ import annotations

import re
import unicodedata

from app import decision_engine as engine
from app.store_quality_runtime import refresh_store_quality_if_needed

# Egyptian shoppers frequently write model-family words phonetically.
engine.SEARCH_ALIASES.setdefault("نوت", "note")
engine.SEARCH_ALIASES.setdefault("ايه", "a")


def normalize_arabic_search(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value.translate(engine.ARABIC_DIGITS))
    normalized = re.sub(r"[\u064b-\u065f\u0670]", "", normalized)
    normalized = normalized.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    normalized = normalized.replace("ى", "ي").replace("ة", "ه")
    normalized = normalized.casefold()
    normalized = re.sub(r"(?<=\d)\s*(?:gb|g|جيجا)\b", " gb", normalized)
    normalized = re.sub(r"[-_/]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    for alias in sorted(engine.SEARCH_ALIASES, key=len, reverse=True):
        normalized = normalized.replace(alias, engine.SEARCH_ALIASES[alias])
    return re.sub(r"\s+", " ", normalized).strip()


# All functions in decision_engine resolve this name at call time. Patching the
# module here fixes search, alternatives, and product comparison consistently.
engine.normalize_arabic_search = normalize_arabic_search

compare_products = engine.compare_products
create_alert_rule = engine.create_alert_rule
create_comparison_share = engine.create_comparison_share
report_price_issue = engine.report_price_issue
smart_search = engine.smart_search


def get_purchase_decision(variant_id: str):
    first = engine.get_purchase_decision(variant_id)
    if not first:
        return first
    store_ids = {
        str(offer.get("store_id"))
        for offer in first.get("cash_offers", [])
        if offer.get("store_id")
    }
    refreshed = False
    for store_id in store_ids:
        try:
            refreshed = (
                refresh_store_quality_if_needed(store_id, max_age_hours=6)
                or refreshed
            )
        except Exception:
            # Store-quality evidence must never make the public price response fail.
            continue
    return engine.get_purchase_decision(variant_id) if refreshed else first
