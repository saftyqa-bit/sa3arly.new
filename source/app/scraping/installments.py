from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Any

from app.schemas import InstallmentPlanExtract
from app.scraping.normalization import normalize_text, parse_price

# These are strong financing-intent markers. Bare duration words such as
# ``month``, ``months`` and ``شهر`` deliberately do not belong here: product
# pages routinely use them for warranty periods (for example, "24 months
# warranty"), which must never create an installment plan. Duration is still
# parsed by MONTH_PATTERNS after a strong marker opens a nearby text segment.
INSTALLMENT_KEYWORDS = (
    "installment",
    "instalment",
    "monthly",
    "قسط",
    "تقسيط",
    "شهريا",
    "شهري",
    "بدون فوائد",
    "0% interest",
)

PROVIDER_ALIASES = {
    "valu": ("Valu", "BNPL"),
    "val u": ("Valu", "BNPL"),
    "contact": ("Contact", "consumer_finance"),
    "souhoola": ("Souhoola", "consumer_finance"),
    "سهولة": ("Souhoola", "consumer_finance"),
    "sympl": ("Sympl", "BNPL"),
    "aman": ("Aman", "consumer_finance"),
    "امان": ("Aman", "consumer_finance"),
    "halan": ("MNT-Halan", "consumer_finance"),
    "حالا": ("MNT-Halan", "consumer_finance"),
    "premium card": ("Premium Card", "card"),
    "cib": ("CIB", "bank"),
    "national bank of egypt": ("National Bank of Egypt", "bank"),
    "nbe": ("National Bank of Egypt", "bank"),
    "البنك الاهلي": ("National Bank of Egypt", "bank"),
    "banque misr": ("Banque Misr", "bank"),
    "بنك مصر": ("Banque Misr", "bank"),
    "qnb": ("QNB Alahli", "bank"),
    "alexbank": ("AlexBank", "bank"),
    "hsbc": ("HSBC", "bank"),
    "mashreq": ("Mashreq", "bank"),
    "adib": ("ADIB", "bank"),
    "arab african": ("AAIB", "bank"),
    "aaib": ("AAIB", "bank"),
    "credit agricole": ("Credit Agricole", "bank"),
    "emirates nbd": ("Emirates NBD", "bank"),
    "nbk": ("NBK Egypt", "bank"),
}

MONTH_PATTERNS = (
    re.compile(r"(?:لمدة|على|حتى)?\s*(\d{1,2})\s*(?:شهر|شهرا|شهور|month|months|mo)\b", re.I),
    re.compile(r"(\d{1,2})\s*(?:installments|instalments|اقساط|قسط)\b", re.I),
)

PAYMENT_PATTERNS = (
    re.compile(
        r"(?:ابتداء\s*من|يبدأ\s*من|starting\s*from|from)?\s*"
        r"((?:\d[\d,٬.٫\s]*))\s*(?:جنيه|ج\.?\s?م|egp)?\s*"
        r"(?:شهريا|شهري|في\s*الشهر|per\s*month|monthly)",
        re.I,
    ),
    re.compile(
        r"(?:قسط|installment|monthly\s*payment)\s*(?:شهري)?\s*[:\-]?\s*"
        r"((?:\d[\d,٬.٫\s]*))\s*(?:جنيه|ج\.?\s?م|egp)?",
        re.I,
    ),
)

TOTAL_PATTERN = re.compile(
    r"(?:اجمالي|الإجمالي|total)\s*(?:المبلغ|cost|amount)?\s*[:\-]?\s*"
    r"((?:\d[\d,٬.٫\s]*))\s*(?:جنيه|ج\.?\s?م|egp)?",
    re.I,
)
DOWN_PATTERN = re.compile(
    r"(?:مقدم|دفعة\s*مقدمة|down\s*payment)\s*[:\-]?\s*((?:\d[\d,٬.٫\s]*))",
    re.I,
)
FEES_PATTERN = re.compile(
    r"(?:مصاريف\s*ادارية|مصاريف\s*إدارية|admin(?:istration)?\s*fees?)\s*[:\-]?\s*"
    r"((?:\d[\d,٬.٫\s]*))",
    re.I,
)


def _segments(text: str) -> Iterable[str]:
    cleaned = re.sub(r"\s+", " ", text)
    for match in re.finditer("|".join(re.escape(x) for x in INSTALLMENT_KEYWORDS), cleaned, re.I):
        start = max(0, match.start() - 260)
        end = min(len(cleaned), match.end() + 420)
        yield cleaned[start:end]


def _provider(
    segment: str,
    provider_aliases: dict[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    normalized = normalize_text(segment)
    aliases: dict[str, tuple[str, str | None]] = dict(PROVIDER_ALIASES)
    for alias, value in (provider_aliases or {}).items():
        if isinstance(value, dict):
            name = str(value.get("name") or alias)
            provider_type = value.get("type")
        elif isinstance(value, (list, tuple)) and value:
            name = str(value[0])
            provider_type = str(value[1]) if len(value) > 1 and value[1] else None
        else:
            name = str(value or alias)
            provider_type = None
        aliases[str(alias)] = (name, provider_type)
    # Long aliases first prevents a short token from winning over a precise name.
    for alias, value in sorted(aliases.items(), key=lambda item: len(normalize_text(item[0])), reverse=True):
        if normalize_text(alias) in normalized:
            return value
    return None, None


def extract_installment_plans(
    text: str,
    *,
    source_url: str,
    cash_price: float | None,
    provider_aliases: dict[str, Any] | None = None,
) -> list[InstallmentPlanExtract]:
    plans: list[InstallmentPlanExtract] = []
    seen: set[str] = set()

    for segment in _segments(text):
        months_values: list[int] = []
        for pattern in MONTH_PATTERNS:
            months_values.extend(int(x) for x in pattern.findall(segment))
        months_values = sorted({x for x in months_values if 1 <= x <= 84})

        payment_values: list[float] = []
        for pattern in PAYMENT_PATTERNS:
            for raw in pattern.findall(segment):
                value = parse_price(raw)
                if value:
                    payment_values.append(value)

        if not months_values and not payment_values:
            continue

        provider_name, provider_type = _provider(segment, provider_aliases)
        starting_from = bool(
            re.search(r"ابتداء\s*من|يبدأ\s*من|starting\s*from|as\s*low\s*as", segment, re.I)
        )
        has_zero_interest = bool(
            re.search(r"بدون\s*فوائد|0\s*%\s*(?:interest|فائدة)|zero\s*interest", segment, re.I)
        )
        has_interest_charge = bool(
            re.search(r"(?:فائدة|interest)\s*(?:بنسبة|rate)?\s*[1-9]\d*(?:[.,]\d+)?\s*%", segment, re.I)
        )
        interest_free = True if has_zero_interest else (False if has_interest_charge else None)
        total = parse_price(TOTAL_PATTERN.search(segment).group(1)) if TOTAL_PATTERN.search(segment) else None
        down = parse_price(DOWN_PATTERN.search(segment).group(1)) if DOWN_PATTERN.search(segment) else None
        fees = parse_price(FEES_PATTERN.search(segment).group(1)) if FEES_PATTERN.search(segment) else None

        pair_count = max(len(months_values), len(payment_values), 1)
        for index in range(pair_count):
            months = months_values[min(index, len(months_values) - 1)] if months_values else None
            payment = payment_values[min(index, len(payment_values) - 1)] if payment_values else None
            calculated = None
            if months and payment:
                calculated = payment * months + (down or 0) + (fees or 0)
            total_effective = total or calculated
            financing_cost = (
                total_effective - cash_price if total_effective is not None and cash_price is not None else None
            )
            markup = (
                financing_cost / cash_price
                if financing_cost is not None and cash_price not in (None, 0)
                else None
            )

            identity = f"{provider_name}|{months}|{payment}|{down}|{fees}|{starting_from}"
            digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)

            completeness = "complete" if months and payment and total_effective is not None else "partial"
            if starting_from:
                completeness = "starting_from_only"

            plans.append(
                InstallmentPlanExtract(
                    provider_name=provider_name,
                    provider_type=provider_type,
                    bank_or_card=provider_name if provider_type in {"bank", "card"} else None,
                    months=months,
                    periodic_payment=payment,
                    down_payment=down,
                    admin_fees=fees,
                    total_published=total,
                    total_calculated=calculated,
                    cash_price_at_observation=cash_price,
                    financing_cost=financing_cost,
                    financing_markup_percent=markup,
                    interest_type="0%" if interest_free is True else ("interest_bearing" if interest_free is False else None),
                    interest_free=interest_free,
                    source_url=source_url,
                    starting_from_only=starting_from,
                    completeness=completeness,
                    raw={"source_segment": segment[:1000]},
                )
            )
    return plans
