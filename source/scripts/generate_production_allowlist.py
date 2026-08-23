from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote

TOKEN_STOPLIST = {
    "gb",
    "ram",
    "storage",
    "w",
    "kg",
    "eg",
    "en",
    "p",
    "5g",
    "4g",
    "lte",
    "dual",
    "sim",
    "كجم",
    "لتر",
    "بوصة",
    "وات",
    "سم",
    "مل",
}
EVIDENCE_SPECIFICATION = re.compile(
    r"اللون|السعة|ram|الذاكرة|الحمولة|مقاس|الحجم",
    re.IGNORECASE,
)
UNIT_SUFFIX = re.compile(
    r"^(\d+(?:\.\d+)?)(?:gb|tb|mb|kg|g|l|ml|w|inch|inches|hz|cm|mm)$",
    re.IGNORECASE,
)


def evidence_tokens(value: str) -> list[str]:
    text = unicodedata.normalize("NFKC", unquote(str(value))).lower()
    text = re.sub(r"[^\w]+|_", " ", text, flags=re.UNICODE)
    tokens = []
    for token in text.split():
        token = UNIT_SUFFIX.sub(r"\1", token)
        if (
            token
            and (len(token) > 1 or token.isdigit())
            and token not in TOKEN_STOPLIST
        ):
            tokens.append(token)
    return tokens


def source_contains_value(source_tokens: set[str], value: str) -> bool:
    expected = evidence_tokens(value)
    return bool(expected) and all(token in source_tokens for token in expected)


def is_verified_presence(product: dict, store: dict) -> bool:
    if (
        store.get("matchConfidence") != "عالية"
        or store.get("reviewStatus") != "جاهز لأول رصد"
    ):
        return False
    if store.get("linkType") == "تغذية منتجات":
        return True
    if store.get("linkType") != "رابط منتج مباشر":
        return False

    source_tokens = set(evidence_tokens(store.get("sourceUrl", "")))
    if not source_contains_value(source_tokens, product.get("brand", "")):
        return False
    if not source_contains_value(source_tokens, product.get("model", "")):
        return False
    return all(
        source_contains_value(source_tokens, specification.get("value", ""))
        for specification in product.get("specs", [])
        if EVIDENCE_SPECIFICATION.search(specification.get("name", ""))
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the exact production mapping allowlist used by the public site"
    )
    parser.add_argument("catalog_json", type=Path)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("db/seed/production_ready_mapping_ids.csv"),
    )
    args = parser.parse_args()

    catalog = json.loads(args.catalog_json.read_text(encoding="utf-8"))
    products = {product["id"]: product for product in catalog["products"]}
    mapping_ids = sorted(
        store["mappingId"]
        for product_id, stores in catalog["presence"].items()
        if (product := products.get(product_id))
        for store in stores
        if is_verified_presence(product, store)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["mapping_id"])
        writer.writerows((mapping_id,) for mapping_id in mapping_ids)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "mapping_count": len(mapping_ids),
                "unique_mapping_count": len(set(mapping_ids)),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
