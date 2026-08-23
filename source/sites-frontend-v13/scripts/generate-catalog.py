from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def optional_number(value: str) -> int | float | None:
    if not value.strip():
        return None
    number = float(value)
    return int(number) if number.is_integer() else number


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: generate-catalog.py /path/to/hourly-engine")

    source = Path(sys.argv[1]).resolve()
    seed = source / "db" / "seed"
    products = read_csv(seed / "product_variants.csv")
    stores = read_csv(seed / "stores.csv")
    mappings = read_csv(seed / "store_product_mappings.csv")
    offers = read_csv(seed / "current_cash_offers.csv")

    mapping_ids: dict[str, set[str]] = defaultdict(set)
    mapping_rows: Counter[str] = Counter()
    connected_store_ids: set[str] = set()
    for mapping in mappings:
        if mapping["active"].lower() != "true":
            continue
        mapping_ids[mapping["variant_id"]].add(mapping["store_id"])
        mapping_rows[mapping["variant_id"]] += 1
        connected_store_ids.add(mapping["store_id"])

    sections = Counter(product["section"] for product in products)
    product_types = {product["product_type"] for product in products if product["product_type"]}
    brands = {product["brand"] for product in products if product["brand"]}
    priced_offers = [
        offer
        for offer in offers
        if offer["active"].lower() == "true" and offer["cash_price"].strip()
    ]

    payload = {
        "generatedAt": datetime.now(UTC).isoformat(),
        "stats": {
            "products": len(products),
            "registryStores": len(stores),
            "activeRegistryStores": sum(store["active"].lower() == "true" for store in stores),
            "connectedStores": len(connected_store_ids),
            "mappings": sum(mapping_rows.values()),
            "sections": len(sections),
            "productTypes": len(product_types),
            "brands": len(brands),
            "pricedOffers": len(priced_offers),
        },
        "sections": [
            {"name": name, "count": count}
            for name, count in sorted(sections.items(), key=lambda item: (-item[1], item[0]))
        ],
        "products": [
            {
                "id": product["variant_id"],
                "name": product["canonical_name"],
                "section": product["section"],
                "type": product["product_type"],
                "brand": product["brand"],
                "model": product["model"],
                "variant": product["variant_name"],
                "ram": optional_number(product["ram_gb"]),
                "storage": optional_number(product["storage_gb"]),
                "color": product["color"],
                "mappedStores": len(mapping_ids[product["variant_id"]]),
                "mappingRows": mapping_rows[product["variant_id"]],
            }
            for product in products
        ],
    }

    destination = source.parent  # retained only to make the source relationship explicit
    del destination
    output = Path(__file__).resolve().parents[1] / "app" / "catalog-data.json"
    output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(output)


if __name__ == "__main__":
    main()
