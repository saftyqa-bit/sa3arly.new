from __future__ import annotations

import csv
import json
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "db" / "seed"


def rows(name: str) -> list[dict[str, str]]:
    with (SEED / name).open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def is_live_cash_price(row: dict[str, str]) -> bool:
    try:
        price = Decimal(row["cash_price"].strip())
    except (InvalidOperation, KeyError):
        return False
    return (
        price > 0
        and row.get("currency", "").upper() == "EGP"
        and row.get("extraction_status", "").lower() == "success"
        and bool(row.get("last_success_at", "").strip())
    )


def main() -> None:
    products = rows("product_variants.csv")
    stores = rows("stores.csv")
    mappings = [
        row
        for row in rows("store_product_mappings.csv")
        if row["active"].lower() == "true"
    ]
    cash = [
        row
        for row in rows("current_cash_offers.csv")
        if row["active"].lower() == "true"
    ]

    stores_per_variant: dict[str, set[str]] = {}
    for mapping in mappings:
        stores_per_variant.setdefault(mapping["variant_id"], set()).add(
            mapping["store_id"]
        )

    comparable_variants = sum(
        len(store_ids) >= 2 for store_ids in stores_per_variant.values()
    )
    connected_stores = len({mapping["store_id"] for mapping in mappings})
    live_prices = sum(is_live_cash_price(row) for row in cash)
    store_concentration = Counter(mapping["store_id"] for mapping in mappings)
    top_store_share = (
        max(store_concentration.values(), default=0) / len(mappings) if mappings else 0
    )

    checks = {
        "has_live_prices": live_prices > 0,
        "has_meaningful_multi_store_comparison": comparable_variants >= 100,
        "connected_store_floor": connected_stores >= 25,
        "top_store_share_below_40_percent": top_store_share < 0.40,
    }
    report = {
        "ready": all(checks.values()),
        "checks": checks,
        "metrics": {
            "products": len(products),
            "registry_stores": len(stores),
            "connected_stores": connected_stores,
            "mapped_variants": len(stores_per_variant),
            "variants_with_two_or_more_stores": comparable_variants,
            "live_cash_prices": live_prices,
            "largest_store_mapping_share": round(top_store_share, 4),
        },
        "message": (
            "Launch gate passed."
            if all(checks.values())
            else "Activation data is structurally usable but public price comparison is not ready yet."
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ready"] else 2)


if __name__ == "__main__":
    main()
