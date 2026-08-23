from __future__ import annotations

from difflib import SequenceMatcher

from app.schemas import MappingTarget
from app.scraping.normalization import (
    extract_ram_gb,
    extract_storage_gb,
    normalize_text,
    normalize_url,
    tokenize,
)
from app.scraping.types import ProductCandidate

CONFLICT_SCORE = -1000.0
AMBIGUITY_MARGIN = 8.0
VARIANT_COLOR_TOKENS = {
    "black",
    "white",
    "blue",
    "green",
    "red",
    "pink",
    "purple",
    "violet",
    "silver",
    "gold",
    "gray",
    "grey",
    "beige",
    "brown",
    "orange",
    "yellow",
    "اسود",
    "أبيض",
    "ابيض",
    "ازرق",
    "أزرق",
    "اخضر",
    "أخضر",
    "احمر",
    "أحمر",
    "فضي",
    "ذهبي",
    "رمادي",
}


def _same_number(expected: float | None, values: set[float]) -> bool | None:
    if expected is None or not values:
        return None
    return any(abs(expected - value) < 0.01 for value in values)


def score_candidate(target: MappingTarget, candidate: ProductCandidate) -> float:
    title = candidate.title or candidate.text
    normalized_title = normalize_text(title)
    target_name = normalize_text(target.canonical_name)
    if not normalized_title:
        return -100.0

    score = 0.0

    if target.gtin and candidate.gtin:
        if normalize_text(target.gtin) == normalize_text(candidate.gtin):
            score += 100
        else:
            return CONFLICT_SCORE

    if target.store_sku and candidate.sku:
        if normalize_text(target.store_sku) == normalize_text(candidate.sku):
            score += 80
        else:
            return CONFLICT_SCORE
    elif target.manufacturer_sku and candidate.sku:
        if normalize_text(target.manufacturer_sku) == normalize_text(candidate.sku):
            score += 80

    brand = normalize_text(target.brand)
    if brand:
        score += 12 if brand in normalized_title else -8

    model = normalize_text(target.model)
    if model:
        model_tokens = tokenize(model)
        title_tokens = tokenize(normalized_title)
        decisive_model_tokens = {
            token
            for token in model_tokens
            if any(character.isdigit() for character in token)
            or token in {"pro", "max", "plus", "ultra", "mini", "air", "fe"}
        }
        if decisive_model_tokens and not decisive_model_tokens.issubset(title_tokens):
            compact_title = normalized_title.replace(" ", "")
            if not all(token.replace(" ", "") in compact_title for token in decisive_model_tokens):
                return CONFLICT_SCORE
        matched = len(model_tokens & title_tokens)
        if matched == len(model_tokens) and matched:
            score += 48
        elif matched:
            score += 15 * (matched / max(len(model_tokens), 1))
        else:
            compact_model = model.replace(" ", "")
            compact_title = normalized_title.replace(" ", "")
            if compact_model and compact_model in compact_title:
                score += 40
            else:
                score -= 45

    target_tokens = tokenize(target_name)
    title_tokens = tokenize(normalized_title)
    if target_tokens and title_tokens:
        jaccard = len(target_tokens & title_tokens) / len(target_tokens | title_tokens)
        score += 25 * jaccard
        score += 10 * SequenceMatcher(None, target_name, normalized_title).ratio()

    expected_storage = target.storage_gb
    capacity_values = extract_storage_gb(normalized_title)
    storage_match = _same_number(expected_storage, capacity_values)
    if storage_match is True:
        score += 18
    elif storage_match is False:
        return CONFLICT_SCORE

    expected_ram = target.ram_gb
    ram_values = extract_ram_gb(normalized_title)
    if (
        not ram_values
        and expected_storage is not None
        and expected_ram is not None
        and _same_number(expected_storage, capacity_values)
    ):
        ram_values = {
            value
            for value in capacity_values
            if abs(value - expected_storage) >= 0.01 and value <= 64
        }
    ram_match = _same_number(expected_ram, ram_values)
    if ram_match is True:
        score += 16
    elif ram_match is False:
        return CONFLICT_SCORE

    color = normalize_text(target.color)
    if color:
        title_color_tokens = tokenize(normalized_title) & VARIANT_COLOR_TOKENS
        target_color_tokens = tokenize(color) & VARIANT_COLOR_TOKENS
        if title_color_tokens and target_color_tokens and title_color_tokens.isdisjoint(target_color_tokens):
            return CONFLICT_SCORE
        score += 6 if color in normalized_title else 0

    known_title = normalize_text(target.title_as_seen)
    if known_title:
        score += 12 * SequenceMatcher(None, known_title, normalized_title).ratio()

    if candidate.price is not None:
        score += 3
    if candidate.url:
        score += 2
    return round(score, 3)


def _same_page_url(first_url: str, second_url: str) -> bool:
    """Compare one product page while ignoring storefront selection queries."""
    return normalize_url(first_url).split("?", 1)[0] == normalize_url(second_url).split("?", 1)[0]


def _same_offer_evidence(first: ProductCandidate, second: ProductCandidate) -> bool:
    """Recognize duplicate representations of one offer without weakening ambiguity checks."""
    if not first.url or not second.url:
        return False
    if not _same_page_url(first.url, second.url):
        return False

    if (
        first.price is not None
        and second.price is not None
        and abs(first.price - second.price) >= 0.01
    ):
        return False

    for field in ("gtin", "sku"):
        first_value = normalize_text(getattr(first, field))
        second_value = normalize_text(getattr(second, field))
        if first_value and second_value and first_value != second_value:
            return False

    first_title = normalize_text(first.title or first.text)
    second_title = normalize_text(second.title or second.text)
    if not first_title or not second_title:
        return False
    if first_title in second_title or second_title in first_title:
        return True
    return SequenceMatcher(None, first_title, second_title).ratio() >= 0.90


def choose_best_candidate(
    target: MappingTarget,
    candidates: list[ProductCandidate],
) -> tuple[ProductCandidate | None, float]:
    scored: list[tuple[float, ProductCandidate]] = []
    seen: set[tuple[str, str | None, float | None]] = set()
    for candidate in candidates:
        key = (normalize_text(candidate.title), candidate.url, candidate.price)
        if key in seen:
            continue
        seen.add(key)
        score = score_candidate(target, candidate)
        if score > CONFLICT_SCORE:
            scored.append((score, candidate))
    if not scored:
        return None, CONFLICT_SCORE
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]
    for other_score, other in scored[1:]:
        if best_score - other_score >= AMBIGUITY_MARGIN:
            break
        if not _same_offer_evidence(best, other):
            return None, best_score
    return best, best_score
