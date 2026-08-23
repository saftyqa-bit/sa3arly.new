from __future__ import annotations

import json
import re
from html import unescape
from typing import Any
from xml.etree import ElementTree

from bs4 import BeautifulSoup, NavigableString

from app.scraping.normalization import (
    normalize_availability,
    normalize_currency,
    normalize_text,
    normalize_url,
    parse_nonnegative_money,
    parse_price,
)
from app.scraping.types import ParsedDocument, ProductCandidate


def _type_contains(value: Any, expected: str) -> bool:
    if isinstance(value, list):
        return any(_type_contains(item, expected) for item in value)
    return normalize_text(str(value)) == normalize_text(expected)


def _walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _brand_name(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("name") or value.get("@id")
    if value:
        return str(value)
    return None


def _image_url(value: Any, base_url: str) -> str | None:
    if isinstance(value, list):
        return next((url for item in value if (url := _image_url(item, base_url))), None)
    if isinstance(value, dict):
        return _image_url(
            value.get("contentUrl") or value.get("url") or value.get("thumbnailUrl"),
            base_url,
        )
    if not value:
        return None
    url = normalize_url(str(value), base_url)
    return url if url.startswith("https://") else None


def _offer_candidates(product: dict[str, Any], base_url: str) -> list[ProductCandidate]:
    name = str(product.get("name") or "").strip()
    sku = product.get("sku") or product.get("mpn")
    gtin = (
        product.get("gtin")
        or product.get("gtin13")
        or product.get("gtin12")
        or product.get("gtin14")
        or product.get("gtin8")
    )
    brand = _brand_name(product.get("brand"))
    image_url = _image_url(product.get("image"), base_url)
    product_url = product.get("url") or base_url
    offers = product.get("offers")

    if isinstance(offers, dict) and _type_contains(offers.get("@type"), "AggregateOffer"):
        low_price = offers.get("lowPrice") or offers.get("price")
        shipping_cost = _extract_shipping(offers)
        return [
            ProductCandidate(
                title=name,
                url=normalize_url(str(product_url), base_url),
                price=parse_price(low_price),
                old_price=parse_price(offers.get("highPrice")),
                currency=normalize_currency(offers.get("priceCurrency")),
                availability=normalize_availability(str(offers.get("availability") or "")),
                seller_name=_brand_name(offers.get("seller")),
                sku=str(sku) if sku else None,
                gtin=str(gtin) if gtin else None,
                brand=brand,
                image_url=image_url,
                shipping_cost=shipping_cost,
                free_shipping=shipping_cost == 0 if shipping_cost is not None else None,
                source_method="jsonld_aggregate_offer",
                text=json.dumps(product, ensure_ascii=False)[:3000],
                raw=product,
            )
        ]

    offer_list: list[dict[str, Any]] = []
    if isinstance(offers, list):
        offer_list = [x for x in offers if isinstance(x, dict)]
    elif isinstance(offers, dict):
        offer_list = [offers]

    if not offer_list:
        return [
            ProductCandidate(
                title=name,
                url=normalize_url(str(product_url), base_url),
                sku=str(sku) if sku else None,
                gtin=str(gtin) if gtin else None,
                brand=brand,
                image_url=image_url,
                source_method="jsonld_product",
                text=json.dumps(product, ensure_ascii=False)[:3000],
                raw=product,
            )
        ]

    out: list[ProductCandidate] = []
    for offer in offer_list:
        specification = offer.get("priceSpecification")
        price = offer.get("price")
        old_price = None
        if isinstance(specification, dict):
            price = price or specification.get("price")
        elif isinstance(specification, list):
            prices = [parse_price(x.get("price")) for x in specification if isinstance(x, dict)]
            prices = [x for x in prices if x is not None]
            if prices:
                price = min(prices)
                old_price = max(prices) if len(prices) > 1 else None

        shipping_cost = _extract_shipping(offer)
        out.append(
            ProductCandidate(
                title=name or str(offer.get("name") or ""),
                url=normalize_url(str(offer.get("url") or product_url), base_url),
                price=parse_price(price),
                old_price=old_price,
                currency=normalize_currency(offer.get("priceCurrency")),
                availability=normalize_availability(str(offer.get("availability") or "")),
                seller_name=_brand_name(offer.get("seller")),
                sku=str(sku) if sku else None,
                gtin=str(gtin) if gtin else None,
                brand=brand,
                image_url=image_url,
                shipping_cost=shipping_cost,
                free_shipping=shipping_cost == 0 if shipping_cost is not None else None,
                source_method="jsonld_offer",
                text=json.dumps(offer, ensure_ascii=False)[:3000],
                raw={"product": product, "offer": offer},
            )
        )
    return out


def _extract_shipping(offer: dict[str, Any]) -> float | None:
    details = offer.get("shippingDetails")
    if isinstance(details, list):
        details = details[0] if details else None
    if not isinstance(details, dict):
        return None
    rate = details.get("shippingRate")
    if isinstance(rate, dict):
        value = rate.get("value")
        if value is None:
            value = rate.get("price")
        return parse_nonnegative_money(value)
    return parse_nonnegative_money(rate)


def _jsonld_candidates(soup: BeautifulSoup, base_url: str) -> list[ProductCandidate]:
    out: list[ProductCandidate] = []
    for script in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        text = script.string or script.get_text(" ", strip=True)
        if not text:
            continue
        text = text.strip().lstrip("\ufeff")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            # Some sites concatenate objects or include invalid control characters.
            try:
                payload = json.loads(re.sub(r"[\x00-\x1f]+", " ", text))
            except json.JSONDecodeError:
                continue

        for node in _walk_json(payload):
            if _type_contains(node.get("@type"), "Product"):
                out.extend(_offer_candidates(node, base_url))
            elif _type_contains(node.get("@type"), "ListItem") and isinstance(node.get("item"), dict):
                item = node["item"]
                if _type_contains(item.get("@type"), "Product"):
                    out.extend(_offer_candidates(item, base_url))
    return out


_LABELED_EGP_PATTERNS = (
    re.compile(
        r"(?:EGP|جنيه(?:\s+مصري)?|ج\s*\.?\s*م)\s*[:\-]?\s*"
        r"([0-9٠-٩۰-۹][0-9٠-٩۰-۹\s,٬.٫]*)",
        re.I,
    ),
    re.compile(
        r"(?<!\w)([0-9٠-٩۰-۹][0-9٠-٩۰-۹\s,٬.٫]*)\s*"
        r"(?:EGP|جنيه(?:\s+مصري)?|ج\s*\.?\s*م)",
        re.I,
    ),
)


def _labeled_egp_price(text: str | None) -> float | None:
    """Return the first explicitly EGP-labelled amount from visible text."""

    if not text:
        return None
    for pattern in _LABELED_EGP_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        price = parse_price(match.group(1))
        if price is not None:
            return price
    return None


def _direct_visible_price(soup: BeautifulSoup) -> float | None:
    """Find a labelled price close to a direct product page's H1.

    Dynamic commerce pages often omit price metadata and add the buy-box after
    JavaScript renders. Restricting this fallback to visible text after the H1
    avoids treating arbitrary numbers or unlabelled monthly payments as cash.
    """

    title_node = soup.select_one("h1")
    if title_node is not None:
        parts: list[str] = []
        total_length = 0
        for element in title_node.next_elements:
            if not isinstance(element, NavigableString):
                continue
            parent_name = getattr(element.parent, "name", "")
            if parent_name in {"script", "style", "noscript"}:
                continue
            value = re.sub(r"\s+", " ", str(element)).strip()
            if not value:
                continue
            parts.append(value)
            total_length += len(value) + 1
            price = _labeled_egp_price(" ".join(parts))
            if price is not None:
                return price
            if total_length >= 8_000:
                break

    main = soup.select_one("main")
    visible_text = (main or soup).get_text(" ", strip=True)
    return _labeled_egp_price(visible_text[:100_000])


def _meta_direct_candidate(soup: BeautifulSoup, base_url: str) -> ProductCandidate | None:
    def content(selector: str) -> str | None:
        node = soup.select_one(selector)
        if not node:
            return None
        return node.get("content") or node.get("value") or node.get_text(" ", strip=True)

    title = (
        content('meta[property="og:title"]')
        or content('meta[name="twitter:title"]')
        or (soup.title.get_text(" ", strip=True) if soup.title else "")
    )
    price_text = (
        content('meta[property="product:price:amount"]')
        or content('meta[itemprop="price"]')
        or content('[itemprop="price"]')
    )
    price = parse_price(price_text)
    visible_price_used = False
    if price is None:
        price = _direct_visible_price(soup)
        visible_price_used = price is not None
    currency = (
        content('meta[property="product:price:currency"]')
        or content('meta[itemprop="priceCurrency"]')
        or "EGP"
    )
    availability = content('meta[property="product:availability"]') or content('[itemprop="availability"]')
    image_url = _image_url(
        content('meta[property="og:image"]') or content('meta[name="twitter:image"]'),
        base_url,
    )
    if not title and not price:
        return None
    return ProductCandidate(
        title=title or "",
        url=base_url,
        price=price,
        currency=normalize_currency(currency),
        availability=normalize_availability(availability),
        image_url=image_url,
        source_method="html_visible_direct" if visible_price_used else "html_meta",
        text="",
        raw={},
    )


def _structured_page_price_exists(
    candidates: list[ProductCandidate],
    fallback: ProductCandidate,
) -> bool:
    """Return whether a trusted parser already priced this exact product page.

    The visible-text fallback is deliberately permissive enough to support
    JavaScript storefronts with no price metadata.  It must not compete with a
    JSON-LD or configured-selector price for the same direct-page URL. Storefront
    page titles and JSON-LD product names often use different marketing wording,
    so exact title equality is not required here. Promotional text such as
    ``EGP 24`` can otherwise create a false near-tie and make a direct product
    page look ambiguous.
    """

    fallback_url = normalize_url(fallback.url)
    if not fallback_url:
        return False

    trusted_methods = {"jsonld_offer", "store_selector_config"}
    return any(
        candidate.price is not None
        and candidate.source_method in trusted_methods
        and normalize_url(candidate.url) == fallback_url
        for candidate in candidates
    )


def _node_title(node) -> str:
    selectors = [
        '[itemprop="name"]',
        ".product-name",
        ".product-title",
        ".product-item-name",
        ".name",
        "h1",
        "h2",
        "h3",
        "h4",
        "a[title]",
    ]
    for selector in selectors:
        found = node.select_one(selector)
        if found:
            text = found.get("content") or found.get("title") or found.get_text(" ", strip=True)
            if text and 3 <= len(text) <= 350:
                return text
    text = node.get_text(" ", strip=True)
    return text[:350] if text else ""


def _node_url(node, base_url: str) -> str | None:
    link = node.select_one('a[href]')
    if not link:
        return None
    href = link.get("href")
    return normalize_url(href, base_url) if href else None


def _price_from_node(node) -> tuple[float | None, float | None, str | None]:
    selectors = [
        '[itemprop="price"]',
        '[data-price]',
        '[data-product-price]',
        ".special-price",
        ".sale-price",
        ".price-final_price",
        ".current-price",
        ".product-price",
        ".price",
    ]
    values: list[float] = []
    currency_text = ""
    # Respect selector priority. Combining every generic `.price` element on a
    # product card can accidentally mix the cash price with a much smaller
    # monthly-installment amount. The first selector that yields valid prices
    # is therefore authoritative; multiple values within that selector still
    # allow sale/regular-price detection.
    for selector in selectors:
        selector_values: list[float] = []
        selector_currency_text = ""
        for found in node.select(selector)[:8]:
            raw = (
                found.get("content")
                or found.get("data-price")
                or found.get("data-product-price")
                or found.get_text(" ", strip=True)
            )
            value = parse_price(raw)
            if value:
                selector_values.append(value)
                selector_currency_text += " " + str(raw)
        if selector_values:
            values = selector_values
            currency_text = selector_currency_text
            break
    if not values:
        # Conservative fallback: only inspect text near currency symbols.
        text = node.get_text(" ", strip=True)
        for raw in re.findall(r"(?:EGP|جنيه|ج\.?\s?م)\s*([\d,٬.٫\s]+)|([\d,٬.٫\s]+)\s*(?:EGP|جنيه|ج\.?\s?م)", text, re.I):
            selected = next((x for x in raw if x), "")
            value = parse_price(selected)
            if value:
                values.append(value)
        currency_text = text
    if not values:
        return None, None, None
    unique = sorted(set(values))
    node_text = normalize_text(node.get_text(" ", strip=True))
    installment_hint = any(
        keyword in node_text
        for keyword in ("شهري", "في الشهر", "قسط", "تقسيط", "monthly", "per month", "installment")
    )
    if len(unique) > 1 and installment_hint and max(unique) / min(unique) >= 3:
        # Separate a small monthly payment from the cash/regular-price cluster.
        high_cluster = [value for value in unique if value >= max(unique) / 2]
        current = min(high_cluster)
        old = max(high_cluster) if len(high_cluster) > 1 and max(high_cluster) > current else None
    else:
        current = min(unique)
        old = max(unique) if len(unique) > 1 and max(unique) > current else None
    return current, old, normalize_currency(currency_text)


def _card_candidates(soup: BeautifulSoup, base_url: str) -> list[ProductCandidate]:
    selectors = [
        '[itemtype*="Product"]',
        "[data-product-id]",
        "[data-sku]",
        ".product-item",
        ".product-card",
        ".product",
        "article.product",
        "li.product",
    ]
    seen_nodes: set[int] = set()
    out: list[ProductCandidate] = []
    for selector in selectors:
        for node in soup.select(selector)[:700]:
            identity = id(node)
            if identity in seen_nodes:
                continue
            seen_nodes.add(identity)
            title = _node_title(node)
            if len(title) < 3:
                continue
            url = _node_url(node, base_url)
            price, old_price, currency = _price_from_node(node)
            sku = node.get("data-sku") or node.get("data-product-sku")
            availability = normalize_availability(node.get_text(" ", strip=True))
            image = node.select_one("img")
            image_url = _image_url(
                image.get("src") or image.get("data-src") or image.get("data-lazy-src")
                if image
                else None,
                base_url,
            )
            out.append(
                ProductCandidate(
                    title=title,
                    url=url,
                    price=price,
                    old_price=old_price,
                    currency=currency,
                    availability=availability,
                    sku=str(sku) if sku else None,
                    image_url=image_url,
                    source_method="html_product_card",
                    text=node.get_text(" ", strip=True)[:1800],
                    raw={},
                )
            )
    return out


def _anchor_candidates(soup: BeautifulSoup, base_url: str) -> list[ProductCandidate]:
    out: list[ProductCandidate] = []
    seen: set[tuple[str, str]] = set()
    for link in soup.select("a[href]")[:3500]:
        text = link.get("title") or link.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text or "").strip()
        if not 3 <= len(text) <= 350:
            continue
        href = link.get("href")
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = normalize_url(href, base_url)
        key = (normalize_text(text), url)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            ProductCandidate(
                title=text,
                url=url,
                source_method="html_link_discovery",
                text=text,
                raw={},
            )
        )
    return out


def _first_selector_value(node, selectors: list[str]) -> str | None:
    for selector in selectors:
        found = node.select_one(selector)
        if found:
            value = (
                found.get("content")
                or found.get("data-price")
                or found.get("value")
                or found.get("title")
                or found.get_text(" ", strip=True)
            )
            if value:
                return str(value)
    return None


def _custom_candidates(soup: BeautifulSoup, base_url: str, config: dict[str, Any] | None) -> list[ProductCandidate]:
    config = config or {}
    card_selectors = config.get("cardSelectors") or []
    title_selectors = config.get("titleSelectors") or []
    price_selectors = config.get("priceSelectors") or []
    old_price_selectors = config.get("oldPriceSelectors") or []
    availability_selectors = config.get("availabilitySelectors") or []
    link_selectors = config.get("linkSelectors") or ["a[href]"]
    sku_selectors = config.get("skuSelectors") or []

    if not any((card_selectors, title_selectors, price_selectors)):
        return []

    nodes = []
    if card_selectors:
        for selector in card_selectors:
            nodes.extend(soup.select(selector)[:1000])
    else:
        nodes = [soup]

    out: list[ProductCandidate] = []
    seen: set[int] = set()
    for node in nodes:
        if id(node) in seen:
            continue
        seen.add(id(node))
        title = _first_selector_value(node, title_selectors) or _node_title(node)
        price = parse_price(_first_selector_value(node, price_selectors))
        old_price = parse_price(_first_selector_value(node, old_price_selectors))
        availability = normalize_availability(_first_selector_value(node, availability_selectors))
        sku = _first_selector_value(node, sku_selectors)
        url = None
        for selector in link_selectors:
            link = node.select_one(selector)
            if link and link.get("href"):
                url = normalize_url(link.get("href"), base_url)
                break
        if title and (price is not None or url):
            out.append(
                ProductCandidate(
                    title=title,
                    url=url or base_url,
                    price=price,
                    old_price=old_price,
                    currency=normalize_currency(node.get_text(" ", strip=True)),
                    availability=availability,
                    sku=sku,
                    source_method="store_selector_config",
                    text=node.get_text(" ", strip=True)[:1800],
                    raw={"selectors_used": True},
                )
            )
    return out


def parse_html(body: str, final_url: str, connector_config: dict[str, Any] | None = None) -> ParsedDocument:
    soup = BeautifulSoup(body, "lxml")
    for tag in soup(["noscript"]):
        tag.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    candidates = _custom_candidates(soup, final_url, connector_config)
    candidates.extend(_jsonld_candidates(soup, final_url))
    meta = _meta_direct_candidate(soup, final_url)
    if meta and not (
        meta.source_method == "html_visible_direct"
        and _structured_page_price_exists(candidates, meta)
    ):
        candidates.append(meta)
    candidates.extend(_card_candidates(soup, final_url))
    links = _anchor_candidates(soup, final_url)

    installment_text_parts: list[str] = []
    for selector in (connector_config or {}).get("installmentSelectors", [])[:30]:
        try:
            installment_text_parts.extend(
                node.get_text(" ", strip=True) for node in soup.select(str(selector))[:100]
            )
        except Exception:
            continue
    visible_text = " ".join(installment_text_parts + [soup.get_text(" ", strip=True)])
    visible_text = re.sub(r"\s+", " ", unescape(visible_text))[:1_500_000]
    return ParsedDocument(
        final_url=final_url,
        title=title,
        visible_text=visible_text,
        candidates=candidates,
        links=links,
        raw_summary={
            "jsonld_or_card_candidates": len(candidates),
            "discovery_links": len(links),
        },
    )


def parse_json_document(body: str, final_url: str) -> ParsedDocument:
    payload = json.loads(body)
    candidates: list[ProductCandidate] = []
    for node in _walk_json(payload):
        possible_name = node.get("name") or node.get("title") or node.get("productName")
        possible_price = (
            node.get("price")
            or node.get("finalPrice")
            or node.get("salePrice")
            or node.get("currentPrice")
        )
        possible_url = node.get("url") or node.get("link") or node.get("productUrl")
        if possible_name and (possible_price is not None or possible_url):
            candidates.append(
                ProductCandidate(
                    title=str(possible_name),
                    url=normalize_url(str(possible_url), final_url) if possible_url else None,
                    price=parse_price(possible_price),
                    old_price=parse_price(node.get("oldPrice") or node.get("regularPrice")),
                    currency=normalize_currency(str(node.get("currency") or "EGP")),
                    availability=normalize_availability(str(node.get("availability") or node.get("stock") or "")),
                    sku=str(node.get("sku") or node.get("mpn") or "") or None,
                    gtin=str(node.get("gtin") or node.get("ean") or "") or None,
                    image_url=_image_url(
                        node.get("image") or node.get("imageUrl") or node.get("thumbnail"),
                        final_url,
                    ),
                    source_method="json_feed",
                    text=json.dumps(node, ensure_ascii=False)[:2500],
                    raw=node,
                )
            )
    text = json.dumps(payload, ensure_ascii=False)
    return ParsedDocument(
        final_url=final_url,
        title="JSON feed",
        visible_text=text[:1_500_000],
        candidates=candidates,
        links=[c for c in candidates if c.url],
        raw_summary={"json_candidates": len(candidates)},
    )


def parse_xml_document(body: str, final_url: str) -> ParsedDocument:
    root = ElementTree.fromstring(body)
    candidates: list[ProductCandidate] = []
    links: list[ProductCandidate] = []

    def local(tag: str) -> str:
        return tag.split("}", 1)[-1].lower()

    for element in root.iter():
        if local(element.tag) in {"item", "product", "entry", "url"}:
            data: dict[str, str] = {}
            for child in list(element):
                key = local(child.tag)
                value = (child.text or "").strip()
                if value:
                    data[key] = value
            title = data.get("title") or data.get("name") or data.get("product_name") or data.get("loc")
            url = data.get("link") or data.get("url") or data.get("loc")
            if title or url:
                candidate = ProductCandidate(
                    title=title or url or "",
                    url=normalize_url(url, final_url) if url else None,
                    price=parse_price(
                        data.get("price") or data.get("sale_price") or data.get("current_price")
                    ),
                    old_price=parse_price(data.get("old_price") or data.get("regular_price")),
                    currency=normalize_currency(data.get("currency")),
                    availability=normalize_availability(data.get("availability") or data.get("stock")),
                    sku=data.get("sku") or data.get("mpn"),
                    gtin=data.get("gtin") or data.get("ean"),
                    image_url=_image_url(
                        data.get("image") or data.get("image_link") or data.get("image_url"),
                        final_url,
                    ),
                    source_method="xml_feed",
                    text=json.dumps(data, ensure_ascii=False),
                    raw=data,
                )
                candidates.append(candidate)
                if candidate.url:
                    links.append(candidate)

    return ParsedDocument(
        final_url=final_url,
        title="XML feed",
        visible_text=re.sub(r"\s+", " ", body)[:1_500_000],
        candidates=candidates,
        links=links,
        raw_summary={"xml_candidates": len(candidates)},
    )


def parse_document(
    body: str,
    final_url: str,
    content_type: str | None,
    connector_config: dict[str, Any] | None = None,
) -> ParsedDocument:
    media = (content_type or "").lower()
    stripped = body.lstrip()
    if "json" in media or stripped.startswith(("{", "[")):
        try:
            return parse_json_document(body, final_url)
        except Exception:
            pass
    if "xml" in media or stripped.startswith("<?xml") or "<urlset" in stripped[:500] or "<rss" in stripped[:500]:
        try:
            return parse_xml_document(body, final_url)
        except Exception:
            pass
    return parse_html(body, final_url, connector_config)
