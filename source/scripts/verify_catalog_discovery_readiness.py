from __future__ import annotations

import csv
import json
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "db" / "seed"


def rows(name: str) -> list[dict[str, str]]:
    with (SEED / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def enabled(value: str) -> bool:
    return value.strip().lower() in {"true", "1", "yes"}


def main() -> None:
    stores = rows("stores.csv")
    connectors = {row["store_id"]: row for row in rows("connector_configs.csv")}
    mappings = [row for row in rows("store_product_mappings.csv") if enabled(row["active"])]
    active = [row for row in stores if enabled(row["active"])]
    problems: list[str] = []
    for store in active:
        store_id = store["store_id"]
        connector = connectors.get(store_id)
        if not connector or not enabled(connector["enabled"]):
            problems.append(f"{store_id}: missing enabled connector")
            continue
        parsed = urlparse(store["base_url"])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            problems.append(f"{store_id}: invalid public base_url")
            continue
        host = parsed.hostname.lower().removeprefix("www.")
        allowed = {
            value.lower().removeprefix("www.")
            for value in connector["allowed_hosts"].split("|")
            if value
        }
        if not any(host == item or host.endswith("." + item) for item in allowed):
            problems.append(f"{store_id}: base host is outside allowed_hosts")
        config = json.loads(connector.get("config_json") or "{}")
        if config.get("location") != "Cairo" or config.get("currency") != "EGP":
            problems.append(f"{store_id}: connector lacks Cairo/EGP defaults")

    mapped_store_ids = {row["store_id"] for row in mappings}
    active_store_ids = {row["store_id"] for row in active}
    report = {
        "ok": not problems,
        "registry_stores": len(stores),
        "active_stores": len(active),
        "catalog_sources_created_on_first_sync": len(active),
        "currently_mapped_active_stores": len(mapped_store_ids & active_store_ids),
        "remaining_active_stores_for_discovery": len(active_store_ids - mapped_store_ids),
        "inactive_registry_stores_preserved": len(stores) - len(active),
        "problems": problems,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if problems:
        raise SystemExit("Catalog discovery readiness verification failed")


if __name__ == "__main__":
    main()
