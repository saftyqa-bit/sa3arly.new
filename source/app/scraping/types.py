from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProductCandidate:
    title: str
    url: str | None = None
    price: float | None = None
    old_price: float | None = None
    currency: str | None = None
    availability: str | None = None
    seller_name: str | None = None
    sku: str | None = None
    gtin: str | None = None
    brand: str | None = None
    image_url: str | None = None
    shipping_cost: float | None = None
    free_shipping: bool | None = None
    source_method: str = "html"
    text: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedDocument:
    final_url: str
    title: str
    visible_text: str
    candidates: list[ProductCandidate]
    links: list[ProductCandidate]
    raw_summary: dict[str, Any] = field(default_factory=dict)
