from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.settings import Settings  # noqa: E402

SEED = ROOT / "db" / "seed"
WINDOW_SECONDS = Settings(_env_file=None).price_run_scheduling_window_seconds


def read(name: str) -> list[dict[str, str]]:
    with (SEED / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    connectors = {row["store_id"]: row for row in read("connector_configs.csv")}
    operational_mapping_ids = {
        row["mapping_id"]
        for row in read("operational_mapping_ids.csv")
        if row.get("mapping_id")
    }
    urls: dict[str, set[str]] = defaultdict(set)
    mapping_counts: dict[str, int] = defaultdict(int)
    for row in read("store_product_mappings.csv"):
        if row.get("active", "").strip().lower() not in {"true", "1", "yes"}:
            continue
        if row["mapping_id"] not in operational_mapping_ids:
            continue
        urls[row["store_id"]].add(row["source_url"])
        mapping_counts[row["store_id"]] += 1

    report = []
    over_capacity = []
    for store_id in sorted(urls):
        connector = connectors[store_id]
        rpm = max(1, int(float(connector["requests_per_minute"])))
        group_count = len(urls[store_id])
        final_offset_seconds = max(0.0, (group_count - 1) * 60.0 / rpm)
        item = {
            "store_id": store_id,
            "mapping_count": mapping_counts[store_id],
            "unique_source_urls": group_count,
            "requests_per_minute": rpm,
            "planned_minutes": round(final_offset_seconds / 60.0, 2),
            "fits_price_run_window": final_offset_seconds <= WINDOW_SECONDS,
        }
        report.append(item)
        if not item["fits_price_run_window"]:
            over_capacity.append(item)

    output = {
        "ok": not over_capacity,
        "window_minutes": WINDOW_SECONDS / 60,
        "total_mappings": sum(mapping_counts.values()),
        "total_unique_store_url_groups": sum(len(v) for v in urls.values()),
        "stores": report,
        "over_capacity": over_capacity,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    if over_capacity:
        raise SystemExit(
            "One or more stores exceed the configured scheduling window; "
            "increase safe RPM, consolidate feeds/category pages, or shard the connector."
        )


if __name__ == "__main__":
    main()
