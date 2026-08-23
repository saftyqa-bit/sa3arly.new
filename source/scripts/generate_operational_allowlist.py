from __future__ import annotations

import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "db" / "seed"
EXCLUDED_URL_TYPES = {"مصدر عام"}
TRUE_VALUES = {"true", "1", "yes"}


def read(name: str) -> list[dict[str, str]]:
    with (SEED / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    connectors = {
        row["store_id"]: row for row in read("connector_configs.csv")
    }
    mappings = read("store_product_mappings.csv")
    operational = sorted(
        row["mapping_id"]
        for row in mappings
        if row.get("active", "").strip().lower() in TRUE_VALUES
        and row.get("url_type") not in EXCLUDED_URL_TYPES
        and connectors.get(row["store_id"], {}).get("enabled", "").strip().lower()
        in TRUE_VALUES
    )

    output = SEED / "operational_mapping_ids.csv"
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["mapping_id"])
        writer.writerows((mapping_id,) for mapping_id in operational)

    excluded = [
        row
        for row in mappings
        if row.get("mapping_id") not in set(operational)
    ]
    print(
        json.dumps(
            {
                "output": str(output),
                "operational_mapping_count": len(operational),
                "excluded_mapping_count": len(excluded),
                "excluded_url_types": sorted(
                    {row.get("url_type", "") for row in excluded}
                ),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
