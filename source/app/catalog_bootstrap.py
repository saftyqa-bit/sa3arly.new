from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlparse, urlunparse

TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_", "source"}
PRODUCT_ROUTE_PARTS = {
    "dp",
    "item",
    "items",
    "p",
    "product",
    "products",
    "product-detail",
    "productdetails",
    "sku",
}
LISTING_ROUTE_PARTS = {
    "c",
    "catalog",
    "categories",
    "category",
    "collection",
    "collections",
    "list",
    "search",
}
NON_PRODUCT_ROUTE_PARTS = {
    "account",
    "blog",
    "cart",
    "checkout",
    "contact",
    "faq",
    "help",
    "login",
    "privacy",
    "returns",
    "signin",
    "signup",
    "terms",
    "wishlist",
}
REJECTED_EXTENSIONS = {
    ".css",
    ".csv",
    ".gif",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".pdf",
    ".png",
    ".svg",
    ".txt",
    ".webp",
    ".xml",
    ".zip",
}

URL_ALIASES = (
    "producturl",
    "productlink",
    "pageurl",
    "sourceurl",
    "canonicalurl",
    "link",
    "url",
)
TITLE_ALIASES = ("productname", "producttitle", "name", "title")
BRAND_ALIASES = ("brandname", "brand", "vendor", "manufacturer")
MERCHANT_SKU_ALIASES = ("variantsku", "storesku", "sku")
MANUFACTURER_SKU_ALIASES = (
    "manufacturersku",
    "manufacturerpartnumber",
    "partnumber",
    "mpn",
    "modelnumber",
)
GTIN_ALIASES = (
    "gtin14",
    "gtin13",
    "gtin12",
    "gtin8",
    "gtin",
    "ean",
    "upc",
    "barcode",
    "variantbarcode",
)
PRICE_ALIASES = (
    "currentprice",
    "saleprice",
    "offerprice",
    "price",
    "lowprice",
    "variantprice",
)
CURRENCY_ALIASES = ("pricecurrency", "currencycode", "currency")
AVAILABILITY_ALIASES = ("stockstatus", "availability", "available", "stock", "instock")
IMAGE_ALIASES = (
    "imageurl",
    "productimage",
    "thumbnail",
    "image",
    "imagesrc",
    "variantimage",
    "allimages",
)
TYPE_ALIASES = ("schematype", "type")


@dataclass(frozen=True)
class NormalizedCatalogRecord:
    source_url: str | None
    normalized_url: str | None
    title: str | None
    brand: str | None
    merchant_sku: str | None
    manufacturer_sku: str | None
    gtin: str | None
    price: float | None
    currency: str | None
    availability: str | None
    image_url: str | None
    validation_status: str
    rejection_code: str | None
    evidence: dict[str, Any]
    data_hash: str
    raw_payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def _text(value: Any, *, limit: int = 1000) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        for key in ("name", "value", "text", "url", "@id"):
            if value.get(key) not in (None, ""):
                return _text(value[key], limit=limit)
        return None
    if isinstance(value, list):
        for item in value:
            result = _text(item, limit=limit)
            if result:
                return result
        return None
    result = re.sub(r"\s+", " ", str(value)).strip()
    return result[:limit] or None


def _top_values(record: dict[str, Any]) -> dict[str, Any]:
    return {_key(key): value for key, value in record.items()}


def _deep_values(value: Any) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}

    def visit(item: Any, prefix: str = "") -> None:
        if isinstance(item, dict):
            for raw_key, child in item.items():
                key = _key(raw_key)
                combined = prefix + key
                if isinstance(child, (dict, list)):
                    visit(child, combined)
                else:
                    out.setdefault(key, []).append(child)
                    out.setdefault(combined, []).append(child)
        elif isinstance(item, list):
            for child in item[:20]:
                visit(child, prefix)

    visit(value)
    return out


def _lookup(
    record: dict[str, Any],
    aliases: tuple[str, ...],
    *,
    deep: dict[str, list[Any]] | None = None,
) -> Any:
    top = _top_values(record)
    for alias in aliases:
        if alias in top and top[alias] not in (None, ""):
            return top[alias]
    nested = deep if deep is not None else _deep_values(record)
    for alias in aliases:
        values = nested.get(alias) or []
        for value in values:
            if value not in (None, ""):
                return value
    return None


def canonical_product_url(value: str) -> str | None:
    try:
        parsed = urlparse(value.strip())
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.lower().rstrip(".")
    port_text = f":{port}" if port and port not in {80, 443} else ""
    path = re.sub(r"/+", "/", unquote(parsed.path or "/"))
    path = path.rstrip("/") or "/"
    query = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=False):
        lowered = key.casefold()
        if lowered.startswith("utm_") or lowered in TRACKING_QUERY_KEYS:
            continue
        query.append((key, item))
    query.sort()
    return urlunparse((parsed.scheme.lower(), host + port_text, path, "", urlencode(query), ""))


def _same_store_host(url: str, allowed_hosts: list[str]) -> bool:
    host = (urlparse(url).hostname or "").casefold().removeprefix("www.")
    allowed = {str(value).casefold().removeprefix("www.").rstrip(".") for value in allowed_hosts if value}
    return bool(host) and bool(allowed) and any(host == item or host.endswith("." + item) for item in allowed)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
    else:
        text = str(value).strip()
        match = re.search(r"(?:\d{1,3}(?:[,. ]\d{3})+|\d+)(?:[.,]\d{1,2})?", text)
        if not match:
            return None
        normalized = match.group(0).replace(" ", "")
        if normalized.count(",") and normalized.count("."):
            normalized = normalized.replace(",", "")
        elif normalized.count(",") == 1 and len(normalized.rsplit(",", 1)[1]) <= 2:
            normalized = normalized.replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
        try:
            number = Decimal(normalized)
        except InvalidOperation:
            return None
    if number <= 0 or number > Decimal("1000000000"):
        return None
    return float(number)


def _gtin(value: Any) -> str | None:
    digits = re.sub(r"\D", "", _text(value, limit=64) or "")
    return digits if len(digits) in {8, 12, 13, 14} else None


def _currency(value: Any, price_value: Any) -> str | None:
    text = f"{_text(value, limit=32) or ''} {_text(price_value, limit=120) or ''}".casefold()
    if "egp" in text or "جنيه" in text or re.search(r"\ble\b", text):
        return "EGP"
    match = re.search(r"\b[A-Z]{3}\b", str(value or "").upper())
    return match.group(0) if match else None


def _availability(value: Any) -> str | None:
    if isinstance(value, bool):
        return "in_stock" if value else "out_of_stock"
    text = _text(value, limit=200)
    if not text:
        return None
    compact = _key(text)
    if compact in {"no", "false", "0"}:
        return "out_of_stock"
    if compact in {"yes", "true", "1"}:
        return "in_stock"
    if "outofstock" in compact or "soldout" in compact or "غيرمتوفر" in compact:
        return "out_of_stock"
    if "instock" in compact or "available" in compact or "متوفر" in compact:
        return "in_stock"
    return text


def _bounded_payload(record: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(record, ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) <= 80_000:
        return record
    return {"truncated": True, "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}


def normalize_external_product_record(
    record: dict[str, Any],
    *,
    allowed_hosts: list[str],
) -> NormalizedCatalogRecord:
    """Normalize one UWS/export record and prove that it is a direct product page.

    Merchant SKUs are deliberately kept separate from manufacturer part numbers.
    Only the latter is eligible for deterministic catalog matching.
    """

    deep = _deep_values(record)
    raw_url = _text(_lookup(record, URL_ALIASES, deep=deep), limit=4000)
    normalized_url = canonical_product_url(raw_url) if raw_url else None
    title = _text(_lookup(record, TITLE_ALIASES, deep=deep), limit=1000)
    brand = _text(_lookup(record, BRAND_ALIASES, deep=deep), limit=250)
    merchant_sku = _text(_lookup(record, MERCHANT_SKU_ALIASES, deep=deep), limit=250)
    manufacturer_sku = _text(_lookup(record, MANUFACTURER_SKU_ALIASES, deep=deep), limit=250)
    gtin = _gtin(_lookup(record, GTIN_ALIASES, deep=deep))
    raw_price = _lookup(record, PRICE_ALIASES, deep=deep)
    price = _number(raw_price)
    currency = _currency(_lookup(record, CURRENCY_ALIASES, deep=deep), raw_price)
    availability = _availability(_lookup(record, AVAILABILITY_ALIASES, deep=deep))
    image_url = _text(_lookup(record, IMAGE_ALIASES, deep=deep), limit=4000)
    structured_type = _text(_lookup(record, TYPE_ALIASES, deep=deep), limit=120)
    if image_url and not image_url.startswith(("http://", "https://")):
        image_url = None

    rejection_code = None
    parts: set[str] = set()
    leaf = ""
    has_product_route = False
    if not raw_url:
        rejection_code = "missing_url"
    elif not normalized_url:
        rejection_code = "invalid_url"
    elif not _same_store_host(normalized_url, allowed_hosts):
        rejection_code = "host_not_allowed"
    else:
        parsed = urlparse(normalized_url)
        lowered_path = parsed.path.casefold()
        parts = {part for part in re.split(r"[/_.-]+", lowered_path) if part}
        leaf = unquote(parsed.path).rstrip("/").rsplit("/", 1)[-1]
        has_product_route = bool(parts.intersection(PRODUCT_ROUTE_PARTS))
        if any(lowered_path.endswith(extension) for extension in REJECTED_EXTENSIONS):
            rejection_code = "non_html_url"
        elif parts.intersection(NON_PRODUCT_ROUTE_PARTS):
            rejection_code = "non_product_route"
        elif structured_type and _key(structured_type) in {
            "organization",
            "website",
            "webpage",
            "localbusiness",
            "breadcrumblist",
        }:
            rejection_code = "structured_type_not_product"
        elif parts.intersection(LISTING_ROUTE_PARTS) and not has_product_route:
            rejection_code = "listing_url"
        elif not title or len(re.sub(r"\s+", "", title)) < 3:
            rejection_code = "missing_product_title"

    evidence_values = {
        "gtin": bool(gtin),
        "manufacturer_sku": bool(manufacturer_sku),
        "brand": bool(brand),
        "price": price is not None,
        "availability": bool(availability),
        "image": bool(image_url),
        "product_route": has_product_route,
        "structured_product_type": bool(structured_type and "product" in _key(structured_type)),
    }
    structured_signal_count = sum(
        int(evidence_values[key])
        for key in (
            "gtin",
            "manufacturer_sku",
            "brand",
            "price",
            "availability",
            "image",
            "structured_product_type",
        )
    )
    if not rejection_code:
        descriptive_leaf = len(re.sub(r"[^a-z0-9\u0600-\u06ff]", "", leaf.casefold())) >= 8
        if has_product_route and structured_signal_count < 1:
            rejection_code = "insufficient_product_evidence"
        elif not has_product_route and (structured_signal_count < 2 or not descriptive_leaf):
            rejection_code = "ambiguous_page_type"

    stable = {
        "url": normalized_url or raw_url,
        "title": title,
        "brand": brand,
        "merchant_sku": merchant_sku,
        "manufacturer_sku": manufacturer_sku,
        "gtin": gtin,
        "price": price,
        "currency": currency,
        "availability": availability,
    }
    data_hash = hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return NormalizedCatalogRecord(
        source_url=raw_url,
        normalized_url=normalized_url,
        title=title,
        brand=brand,
        merchant_sku=merchant_sku,
        manufacturer_sku=manufacturer_sku,
        gtin=gtin,
        price=price,
        currency=currency,
        availability=availability,
        image_url=image_url,
        validation_status="accepted" if not rejection_code else "rejected",
        rejection_code=rejection_code,
        evidence={
            **evidence_values,
            "structured_signal_count": structured_signal_count,
        },
        data_hash=data_hash,
        raw_payload=_bounded_payload(record),
    )


def normalize_external_product_records(
    records: list[dict[str, Any]],
    *,
    allowed_hosts: list[str],
) -> list[NormalizedCatalogRecord]:
    return [normalize_external_product_record(record, allowed_hosts=allowed_hosts) for record in records]
