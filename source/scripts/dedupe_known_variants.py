from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "db" / "seed"

ALIASES = {
    "VAR-1AE723AC066BBA": (
        "VAR-F772E1F9D9F5E9",
        "Exact Tefal manufacturer SKU TY6A35EG across stores",
    ),
    "VAR-8FF7F23D7B7D62": (
        "VAR-CF608D463564F0",
        "Exact Tefal manufacturer SKU FV2831E2 across stores",
    ),
    "VAR-80DE4C250FFB37": (
        "VAR-15F8FB3380CB00",
        "Exact Tefal manufacturer SKU FV5751E0 across stores",
    ),
    "VAR-99F499D606863F": (
        "VAR-D451AA5E3989FF",
        "Exact TP-Link manufacturer SKU TL-WN725N across stores",
    ),
}


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    products_path = SEED / "product_variants.csv"
    fields, products = read_rows(products_path)
    products = [row for row in products if row["variant_id"] not in ALIASES]
    write_rows(products_path, fields, products)

    for filename in (
        "store_product_mappings.csv",
        "current_cash_offers.csv",
        "installment_discovery_tasks.csv",
    ):
        path = SEED / filename
        fields, rows = read_rows(path)
        for row in rows:
            if row["variant_id"] in ALIASES:
                row["variant_id"] = ALIASES[row["variant_id"]][0]
        write_rows(path, fields, rows)

    write_rows(
        SEED / "variant_aliases.csv",
        ["alias_variant_id", "canonical_variant_id", "reason"],
        [
            {
                "alias_variant_id": alias_id,
                "canonical_variant_id": canonical_id,
                "reason": reason,
            }
            for alias_id, (canonical_id, reason) in ALIASES.items()
        ],
    )

    metadata_path = SEED / "seed_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    mapping_rows = read_rows(SEED / "store_product_mappings.csv")[1]
    mapped_variants = {row["variant_id"] for row in mapping_rows}
    counts = metadata.setdefault("counts", {})
    counts.update(
        {
            "product_variants": len(products),
            "mapped_product_variants": len(mapped_variants),
            "awaiting_mapping_variants": len(products) - len(mapped_variants),
            "known_variant_aliases": len(ALIASES),
        }
    )
    for legacy_root_key in (
        "product_variants",
        "mapped_product_variants",
        "awaiting_mapping_variants",
        "known_variant_aliases",
    ):
        metadata.pop(legacy_root_key, None)
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "products": len(products),
                "mapped_variants": len(mapped_variants),
                "aliases": len(ALIASES),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
