from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Any
from urllib.parse import unquote, urlparse

from app.schemas import MappingTarget
from app.scraping.matching import CONFLICT_SCORE, score_candidate
from app.scraping.normalization import normalize_text, tokenize
from app.scraping.types import ProductCandidate


def _compact(value: str | None) -> str:
    return re.sub(r"[^a-z0-9\u0600-\u06ff]+", "", normalize_text(value))


def _unique(values: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    unique = {str(row["variant_id"]): row for row in values}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _distinctive_url_sku(value: str) -> bool:
    if value.isdigit():
        return len(value) >= 8
    return len(value) >= 6 and any(char.isalpha() for char in value) and any(
        char.isdigit() for char in value
    )


def _distinctive_model_anchor(value: str) -> bool:
    compact = _compact(value)
    if len(compact) < 3:
        return False
    has_alpha = any(char.isalpha() for char in compact)
    has_digit = any(char.isdigit() for char in compact)
    return (has_alpha and has_digit) or (has_alpha and len(compact) >= 5)


def build_catalog_variant_index(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact, reusable index for deterministic catalog matching.

    Catalog scans can emit tens of thousands of product URLs. Loading the
    2,000-3,000 known variants once per ingestion batch is both faster and
    safer than lowering the fuzzy-match threshold for every URL.
    """

    by_gtin: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_sku: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_brand: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_model_anchor: dict[str, list[dict[str, Any]]] = defaultdict(list)
    indexed: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        if row.get("active") is False or row.get("source_status") == "catalog_provisional":
            continue
        indexed.append(row)
        gtin = re.sub(r"\D", "", str(row.get("gtin") or ""))
        if len(gtin) in {8, 12, 13, 14}:
            by_gtin[gtin].append(row)
        sku = _compact(str(row.get("manufacturer_sku") or ""))
        if len(sku) >= 4:
            by_sku[sku].append(row)
        brand = normalize_text(row.get("brand"))
        if brand:
            by_brand[brand].append(row)
        for token in tokenize(row.get("model")):
            if _distinctive_model_anchor(token):
                by_model_anchor[token].append(row)
    return {
        "rows": indexed,
        "by_gtin": dict(by_gtin),
        "by_sku": dict(by_sku),
        "by_brand": dict(by_brand),
        "by_model_anchor": dict(by_model_anchor),
        "brands": sorted(by_brand, key=len, reverse=True),
    }


def _target(row: dict[str, Any], store_id: str) -> MappingTarget:
    return MappingTarget(
        mapping_id="catalog-reconcile",
        offer_id="catalog-reconcile",
        offer_key="catalog-reconcile",
        variant_id=str(row["variant_id"]),
        store_id=store_id,
        source_url="https://catalog.invalid/product",
        canonical_name=str(row.get("canonical_name") or ""),
        section=row.get("section"),
        product_type=row.get("product_type"),
        brand=row.get("brand"),
        model=row.get("model"),
        variant_name=row.get("variant_name"),
        ram_gb=float(row["ram_gb"]) if row.get("ram_gb") is not None else None,
        storage_gb=float(row["storage_gb"]) if row.get("storage_gb") is not None else None,
        color=row.get("color"),
        manufacturer_sku=row.get("manufacturer_sku"),
        gtin=row.get("gtin"),
    )


def deterministic_catalog_match(
    index: dict[str, Any],
    item: dict[str, Any],
    *,
    store_id: str,
) -> tuple[dict[str, Any] | None, float, str | None]:
    """Return only identifier-safe or unique brand/model matches.

    A plain fuzzy title score is intentionally insufficient. A brand/model
    match must be distinctive, variant-safe, and unique in the known catalog;
    ambiguous colour/storage variants remain in review.
    """

    title = str(item.get("title") or "")
    source_url = str(item.get("normalized_url") or item.get("source_url") or "")
    path_text = unquote(urlparse(source_url).path).replace("/", " ")
    searchable = normalize_text(f"{title} {path_text}")
    searchable_tokens = tokenize(searchable)
    compact_searchable = _compact(searchable)

    gtin = re.sub(r"\D", "", str(item.get("gtin") or ""))
    if len(gtin) in {8, 12, 13, 14}:
        exact = _unique(index["by_gtin"].get(gtin, []))
        if exact:
            return exact, 200.0, "catalog_exact_gtin"

    candidate_sku = _compact(str(item.get("sku") or ""))
    if len(candidate_sku) >= 4:
        exact = _unique(index["by_sku"].get(candidate_sku, []))
        if exact:
            return exact, 185.0, "catalog_exact_manufacturer_sku"

    # Sitemaps rarely expose structured SKU fields, but product slugs often
    # contain the manufacturer's model code. Match such codes only when they
    # identify exactly one known variant.
    sku_hits = []
    for sku, rows in index["by_sku"].items():
        if _distinctive_url_sku(sku) and sku in compact_searchable:
            sku_hits.extend(rows)
    exact = _unique(sku_hits)
    if exact:
        return exact, 175.0, "catalog_url_manufacturer_sku"

    candidate_brand = normalize_text(item.get("brand"))
    brand_keys: list[str] = []
    if candidate_brand in index["by_brand"]:
        brand_keys.append(candidate_brand)
    for brand in index["brands"]:
        if brand in brand_keys:
            continue
        brand_tokens = tokenize(brand)
        if brand_tokens and brand_tokens.issubset(searchable_tokens):
            brand_keys.append(brand)

    candidate_rows: dict[str, dict[str, Any]] = {}
    for brand in brand_keys:
        for row in index["by_brand"].get(brand, []):
            candidate_rows[str(row["variant_id"])] = row
    for token in searchable_tokens:
        for row in index["by_model_anchor"].get(token, []):
            candidate_rows[str(row["variant_id"])] = row

    candidate = ProductCandidate(
        title=title,
        url=source_url or None,
        price=item.get("price"),
        old_price=item.get("old_price"),
        currency=item.get("currency"),
        availability=item.get("availability"),
        sku=item.get("sku"),
        gtin=item.get("gtin"),
        brand=item.get("brand"),
        source_method=item.get("source_method") or "catalog_discovery",
        text=str(item.get("text") or title),
        raw=item.get("raw") or {},
    )
    matches: list[dict[str, Any]] = []
    for row in candidate_rows.values():
        model = normalize_text(row.get("model"))
        if not model:
            continue
        model_tokens = tokenize(model)
        compact_model = _compact(model)
        distinctive = any(any(char.isdigit() for char in token) for token in model_tokens)
        distinctive = distinctive or len(compact_model) >= 6
        if not distinctive:
            continue
        model_present = bool(compact_model and compact_model in compact_searchable)
        model_present = model_present or bool(
            model_tokens and model_tokens.issubset(searchable_tokens)
        )
        if not model_present:
            continue
        if score_candidate(_target(row, store_id), candidate) <= CONFLICT_SCORE:
            continue
        matches.append(row)

    exact = _unique(matches)
    if exact:
        return exact, 150.0, "catalog_unique_brand_model"
    return None, -10000.0, None


def catalog_candidate_has_match_evidence(
    index: dict[str, Any],
    item: dict[str, Any],
) -> bool:
    """Return whether a candidate justifies the slower PostgreSQL fuzzy lookup."""

    if item.get("gtin") or item.get("sku"):
        return True
    title = str(item.get("title") or "")
    source_url = str(item.get("normalized_url") or item.get("source_url") or "")
    searchable = normalize_text(
        f"{title} {unquote(urlparse(source_url).path).replace('/', ' ')}"
    )
    searchable_tokens = tokenize(searchable)
    for brand in index["brands"]:
        brand_tokens = tokenize(brand)
        if brand_tokens and brand_tokens.issubset(searchable_tokens):
            return True
    return any(token in index["by_model_anchor"] for token in searchable_tokens)
