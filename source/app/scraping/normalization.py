from __future__ import annotations

import math
import re
import unicodedata
from urllib.parse import urljoin, urlparse, urlunparse

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
ARABIC_MARKS_RE = re.compile(r"[\u064B-\u065F\u0670\u06D6-\u06ED]")
SPACE_RE = re.compile(r"\s+")
NUMBER_RE = re.compile(r"[-+]?\d[\d\s,٬.٫]*")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).translate(ARABIC_DIGITS)
    text = ARABIC_MARKS_RE.sub("", text)
    text = text.lower()
    text = text.replace("ـ", "")
    text = re.sub(r"[^\w\u0600-\u06FF.+/-]+", " ", text, flags=re.UNICODE)
    return SPACE_RE.sub(" ", text).strip()


def tokenize(value: str | None) -> set[str]:
    text = normalize_text(value)
    return {t for t in text.split() if len(t) > 1}


def parse_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None

    text = str(value).translate(ARABIC_DIGITS).strip()
    match = NUMBER_RE.search(text)
    if not match:
        return None
    raw = match.group(0).replace(" ", "").replace("٬", ",").replace("٫", ".")

    if "," in raw and "." in raw:
        if raw.rfind(".") > raw.rfind(","):
            raw = raw.replace(",", "")
        else:
            raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        parts = raw.split(",")
        if len(parts[-1]) in {1, 2} and len(parts) == 2:
            raw = ".".join(parts)
        else:
            raw = "".join(parts)
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")
    elif "." in raw:
        left, right = raw.rsplit(".", 1)
        if len(right) == 3 and len(left) >= 1:
            raw = left + right

    try:
        number = float(raw)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def parse_price(value: object) -> float | None:
    number = parse_number(value)
    if number is None or number <= 0:
        return None
    return round(number, 2)


def parse_nonnegative_money(value: object) -> float | None:
    number = parse_number(value)
    if number is None or number < 0:
        return None
    return round(number, 2)


def normalize_currency(value: str | None, default: str = "EGP") -> str:
    text = normalize_text(value)
    if any(x in text for x in ("egp", "جنيه", "ج م", "ج.م")):
        return "EGP"
    if "usd" in text or "$" in (value or ""):
        return "USD"
    if "eur" in text or "€" in (value or ""):
        return "EUR"
    if "sar" in text or "ريال سعودي" in text:
        return "SAR"
    if "aed" in text or "درهم" in text:
        return "AED"
    return default


def normalize_availability(value: str | None) -> str | None:
    text = normalize_text(value)
    if not text:
        return None
    unavailable = (
        "outofstock",
        "out of stock",
        "sold out",
        "غير متوفر",
        "نفد",
        "غير متاح",
        "unavailable",
    )
    limited = ("limited", "كمية محدودة", "اخر قطعة", "last piece")
    preorder = ("preorder", "pre order", "طلب مسبق", "احجز")
    available = ("instock", "in stock", "متوفر", "available", "اضف للسلة", "add to cart")
    if any(x in text for x in unavailable):
        return "out_of_stock"
    if any(x in text for x in limited):
        return "limited"
    if any(x in text for x in preorder):
        return "preorder"
    if any(x in text for x in available):
        return "available"
    return "unknown"


def normalize_url(url: str, base_url: str | None = None) -> str:
    absolute = urljoin(base_url or "", url.strip())
    parsed = urlparse(absolute)
    host = (parsed.hostname or "").lower()
    port = f":{parsed.port}" if parsed.port and parsed.port not in (80, 443) else ""
    path = re.sub(r"/+", "/", parsed.path or "/")
    return urlunparse((parsed.scheme.lower(), host + port, path.rstrip("/") or "/", "", parsed.query, ""))


def extract_storage_gb(text: str | None) -> set[float]:
    normalized = normalize_text(text)
    out: set[float] = set()
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(tb|gb|تيرا|جيجا)", normalized):
        value = float(match.group(1))
        unit = match.group(2)
        if unit in {"tb", "تيرا"}:
            value *= 1024
        out.add(value)
    return out


def extract_ram_gb(text: str | None) -> set[float]:
    normalized = normalize_text(text)
    out: set[float] = set()
    patterns = (
        r"(\d+(?:\.\d+)?)\s*(?:gb|جيجا)\s*(?:ram|رام)",
        r"(?:ram|رام)\s*(\d+(?:\.\d+)?)\s*(?:gb|جيجا)?",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, normalized):
            out.add(float(match.group(1)))
    return out
