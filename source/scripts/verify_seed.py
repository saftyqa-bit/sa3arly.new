from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "db" / "seed"


def rows(name: str):
    with (SEED / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def unique_check(data, field: str, label: str):
    counts = Counter(row[field] for row in data)
    duplicates = [key for key, value in counts.items() if key and value > 1]
    if duplicates:
        raise AssertionError(f"{label} has duplicate {field}: {duplicates[:10]}")


def main():
    products = rows("product_variants.csv")
    stores = rows("stores.csv")
    mappings = rows("store_product_mappings.csv")
    cash = rows("current_cash_offers.csv")
    installments = rows("installment_discovery_tasks.csv")
    connectors = rows("connector_configs.csv")
    aliases = rows("variant_aliases.csv")

    unique_check(products, "variant_id", "products")
    unique_check(stores, "store_id", "stores")
    unique_check(mappings, "mapping_id", "mappings")
    unique_check(mappings, "offer_key", "mappings")
    unique_check(cash, "offer_id", "cash")
    unique_check(cash, "offer_key", "cash")
    unique_check(cash, "mapping_id", "cash")
    unique_check(installments, "task_id", "installments")
    unique_check(installments, "cash_offer_key", "installments")
    unique_check(aliases, "alias_variant_id", "aliases")

    product_ids = {x["variant_id"] for x in products}
    alias_ids = {x["alias_variant_id"] for x in aliases}
    store_ids = {x["store_id"] for x in stores}
    mapping_ids = {x["mapping_id"] for x in mappings}
    mapping_by_id = {x["mapping_id"]: x for x in mappings}
    mapping_by_offer = {x["offer_key"]: x for x in mappings}
    connector_by_store = {x["store_id"]: x for x in connectors}

    assert set(connector_by_store) == store_ids
    assert alias_ids.isdisjoint(product_ids)
    assert all(x["canonical_variant_id"] in product_ids for x in aliases)
    assert all(
        x["alias_variant_id"] != x["canonical_variant_id"] for x in aliases
    )
    assert all(x["canonical_variant_id"] not in alias_ids for x in aliases)
    assert all(x["variant_id"] in product_ids for x in mappings)
    assert all(x["store_id"] in store_ids for x in mappings)
    assert all(x["mapping_id"] in mapping_ids for x in cash)
    assert all(x["cash_offer_key"] in mapping_by_offer for x in installments)
    assert len(mappings) == len(cash) == len(installments)

    for connector in connectors:
        assert int(connector["requests_per_minute"]) > 0
        assert int(connector["max_concurrency"]) > 0

    for offer in cash:
        mapping = mapping_by_id[offer["mapping_id"]]
        assert offer["offer_key"] == mapping["offer_key"]
        assert offer["variant_id"] == mapping["variant_id"]
        assert offer["store_id"] == mapping["store_id"]

    for task in installments:
        mapping = mapping_by_offer[task["cash_offer_key"]]
        assert task["variant_id"] == mapping["variant_id"]
        assert task["store_id"] == mapping["store_id"]

    for mapping in mappings:
        parsed = urlparse(mapping["source_url"])
        assert parsed.scheme in {"http", "https"}
        assert parsed.username is None and parsed.password is None
        host = (parsed.hostname or "").lower().removeprefix("www.")
        allowed = {
            value.lower().removeprefix("www.")
            for value in connector_by_store[mapping["store_id"]]["allowed_hosts"].split("|")
            if value
        }
        assert host and any(host == item or host.endswith("." + item) for item in allowed), (
            mapping["mapping_id"],
            host,
            allowed,
        )

    metadata = json.loads((SEED / "seed_metadata.json").read_text(encoding="utf-8"))
    active_mappings = [x for x in mappings if x["active"].lower() == "true"]
    actual_counts = {
        "product_variants": len(products),
        "mapped_product_variants": len({x["variant_id"] for x in active_mappings}),
        "awaiting_mapping_variants": len(products)
        - len({x["variant_id"] for x in active_mappings}),
        "stores": len(stores),
        "stores_with_current_mappings": len(
            {x["store_id"] for x in active_mappings}
        ),
        "store_product_mappings": len(mappings),
        "initial_cash_offer_rows": len(cash),
        "installment_discovery_tasks": len(installments),
        "initial_unique_store_url_groups": len(
            {(x["store_id"], x["source_url"]) for x in active_mappings}
        ),
        "known_variant_aliases": len(aliases),
    }
    for field, value in actual_counts.items():
        assert metadata["counts"].get(field) == value, (
            field,
            metadata["counts"].get(field),
            value,
        )
    print(
        json.dumps(
            {
                "ok": True,
                "counts": {
                    "products": len(products),
                    "stores": len(stores),
                    "mappings": len(mappings),
                    "cash_offers": len(cash),
                    "installment_tasks": len(installments),
                    "variant_aliases": len(aliases),
                },
                "metadata": metadata["counts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
