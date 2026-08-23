from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from app.scraping.normalization import normalize_text


@dataclass(frozen=True)
class CatalogEntityIdentity:
    entity_id: str
    identity_key: str
    strength: int


def _compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u0600-\u06ff]+", "", normalize_text(str(value or "")))


def valid_gtin(value: Any) -> str | None:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) in {8, 12, 13, 14} else None


def catalog_entity_identity(item: dict[str, Any], *, store_id: str) -> CatalogEntityIdentity:
    gtin = valid_gtin(item.get("gtin"))
    brand = _compact(item.get("brand"))
    manufacturer_sku = _compact(
        item.get("manufacturer_sku") or item.get("sku")
    )
    normalized_url = str(
        item.get("normalized_url") or item.get("source_url") or ""
    ).strip()
    if gtin:
        key = f"gtin:{gtin}"
        strength = 100
    elif brand and manufacturer_sku and len(manufacturer_sku) >= 4:
        key = f"mpn:{brand}:{manufacturer_sku}"
        strength = 90
    else:
        key = f"store_url:{store_id}:{normalized_url}"
        strength = 60
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20].upper()
    return CatalogEntityIdentity(f"CPE-{digest}", key, strength)


def catalog_observation_publishable(
    item: dict[str, Any],
    *,
    origin_type: str,
) -> bool:
    if str(item.get("validation_status") or "accepted") == "rejected":
        return False
    if not str(item.get("normalized_url") or item.get("source_url") or "").startswith(
        ("http://", "https://")
    ):
        return False
    if len(normalize_text(str(item.get("title") or ""))) < 3:
        return False
    try:
        price = float(item.get("price") or item.get("observed_price") or 0)
    except (TypeError, ValueError):
        return False
    if price < 10:
        return False
    if str(item.get("currency") or "EGP").upper() != "EGP":
        return False
    if str(item.get("availability") or "").lower() in {
        "out_of_stock",
        "unavailable",
    }:
        return False
    if origin_type == "catalog_import":
        evidence = item.get("evidence") or {}
        return bool(evidence.get("price")) and int(
            evidence.get("structured_signal_count") or 0
        ) >= 2
    return bool(
        valid_gtin(item.get("gtin"))
        or (item.get("brand") and item.get("sku"))
        or str(item.get("source_method") or "").startswith(
            ("jsonld", "microdata", "html_visible_direct")
        )
    )


def catalog_technical_specs(title: str | None) -> dict[str, float | None]:
    """Extract conservative RAM/storage values for hierarchy filters."""

    normalized = normalize_text(title)
    ram_values: set[float] = set()
    capacity_values: set[float] = set()
    for match in re.finditer(
        r"(\d+(?:\.\d+)?)\s*(gb|tb|جيجا|تيرا)(?:\s*(ram|رام))?",
        normalized,
    ):
        value = float(match.group(1))
        if match.group(2) in {"tb", "تيرا"}:
            value *= 1024
        if match.group(3):
            ram_values.add(value)
        else:
            capacity_values.add(value)
    for match in re.finditer(r"\b(\d{2,4})\s*[/+]\s*(\d{1,3})\b", normalized):
        first, second = float(match.group(1)), float(match.group(2))
        capacity_values.add(max(first, second))
        ram_values.add(min(first, second))
    non_ram_capacity = capacity_values - ram_values
    return {
        "ram_gb": max(ram_values) if ram_values else None,
        "storage_gb": max(non_ram_capacity or capacity_values)
        if capacity_values
        else None,
    }
