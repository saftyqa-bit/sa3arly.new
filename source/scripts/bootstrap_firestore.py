from __future__ import annotations

import argparse
import csv
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.firestore_repository import (
    CASH_MONEY_FIELDS,
    FirestoreRepository,
    _safe_doc_id,
    amount_to_minor,
)
from app.scraping.normalization import normalize_url

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "db" / "seed"
BATCH_SIZE = 400


def read_rows(name: str) -> list[dict[str, str]]:
    with (SEED / name).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def null(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value if value else None
    return value


def boolean(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "نعم", "صح"}


def optional_boolean(value: Any) -> bool | None:
    return boolean(value) if null(value) is not None else None


def number(value: Any) -> float | None:
    return float(value) if null(value) is not None else None


def integer(value: Any) -> int:
    return int(float(value)) if null(value) is not None else 0


def dt(value: Any) -> datetime | None:
    value = null(value)
    if value is None:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def write_documents(
    repository: FirestoreRepository,
    collection_name: str,
    documents: Iterable[tuple[str, dict[str, Any]]],
    *,
    merge: bool = True,
) -> int:
    batch = repository.client.batch()
    pending = 0
    written = 0
    for doc_id, payload in documents:
        batch.set(
            repository._col(collection_name).document(_safe_doc_id(doc_id)),
            payload,
            merge=merge,
        )
        pending += 1
        written += 1
        if pending >= BATCH_SIZE:
            batch.commit()
            batch = repository.client.batch()
            pending = 0
    if pending:
        batch.commit()
    return written


def existing_ids(repository: FirestoreRepository, collection_name: str) -> set[str]:
    return {snapshot.id for snapshot in repository._col(collection_name).stream()}


def product_documents(rows: list[dict[str, str]]):
    for row in rows:
        payload = {
            **row,
            "ram_gb": number(row.get("ram_gb")),
            "storage_gb": number(row.get("storage_gb")),
            "created_at": dt(row.get("created_at")),
            "updated_at": dt(row.get("updated_at")),
            "specs": {},
        }
        yield row["variant_id"], {key: null(value) for key, value in payload.items()}


def store_documents(rows: list[dict[str, str]]):
    for row in rows:
        payload = {
            **row,
            "current_mapping_count": integer(row.get("current_mapping_count")),
            "ready_mapping_count": integer(row.get("ready_mapping_count")),
            "active": boolean(row.get("active")),
            "updated_at": datetime.now(UTC),
        }
        yield row["store_id"], {key: null(value) for key, value in payload.items()}


def connector_documents(rows: list[dict[str, str]]):
    for row in rows:
        payload = {
            "store_id": row["store_id"],
            "mode": row.get("mode") or "auto",
            "allowed_hosts": [
                value for value in (row.get("allowed_hosts") or "").split("|") if value
            ],
            "requests_per_minute": max(integer(row.get("requests_per_minute")), 1),
            "max_concurrency": max(integer(row.get("max_concurrency")), 1),
            "browser_required": boolean(row.get("browser_required")),
            "respect_robots": boolean(row.get("respect_robots")),
            "enabled": boolean(row.get("enabled")),
            "version": row.get("version") or "generic-v1",
            "config": json.loads(row.get("config_json") or "{}"),
            "updated_at": datetime.now(UTC),
        }
        yield row["store_id"], payload


def mapping_documents(
    rows: list[dict[str, str]],
    products: dict[str, dict[str, Any]],
    stores: dict[str, dict[str, Any]],
    connectors: dict[str, dict[str, Any]],
    operational_mapping_ids: set[str],
):
    for row in rows:
        product = products[row["variant_id"]]
        store = stores[row["store_id"]]
        connector = connectors[row["store_id"]]
        operational = (
            boolean(row.get("active"))
            and row["mapping_id"] in operational_mapping_ids
        )
        payload = {
            **row,
            "normalized_url": normalize_url(row["source_url"]),
            "evidence_count": integer(row.get("evidence_count")),
            "evidence_verified_at": dt(row.get("evidence_verified_at")),
            "active": operational,
            "created_at": dt(row.get("created_at")),
            "updated_at": dt(row.get("updated_at")),
            "metadata": {},
            "canonical_name": product.get("canonical_name"),
            "section": product.get("section"),
            "product_type": product.get("product_type"),
            "brand": product.get("brand"),
            "model": product.get("model"),
            "variant_name": product.get("variant_name"),
            "ram_gb": product.get("ram_gb"),
            "storage_gb": product.get("storage_gb"),
            "color": product.get("color"),
            "manufacturer_sku": product.get("manufacturer_sku"),
            "gtin": product.get("gtin"),
            "store_name": store.get("name"),
            "store_base_url": store.get("base_url"),
            "store_active": store.get("active", True),
            "priority": store.get("priority"),
            "connector_mode": connector.get("mode"),
            "allowed_hosts": connector.get("allowed_hosts") or [],
            "requests_per_minute": connector.get("requests_per_minute"),
            "max_concurrency": connector.get("max_concurrency"),
            "browser_required": connector.get("browser_required"),
            "respect_robots": connector.get("respect_robots"),
            "connector_enabled": connector.get("enabled"),
            "connector_version": connector.get("version"),
            "connector_config": connector.get("config") or {},
        }
        yield row["mapping_id"], {key: null(value) for key, value in payload.items()}


def cash_offer_documents(
    rows: list[dict[str, str]],
    products: dict[str, dict[str, Any]],
    stores: dict[str, dict[str, Any]],
):
    numeric_fields = {
        "available_quantity",
        "purchase_limit",
        "min_delivery_days",
        "max_delivery_days",
        "warranty_months",
        "discount_percent",
    }
    for row in rows:
        product = products[row["variant_id"]]
        store = stores[row["store_id"]]
        payload: dict[str, Any] = {
            key: null(value)
            for key, value in row.items()
            if key not in CASH_MONEY_FIELDS
        }
        for field in CASH_MONEY_FIELDS:
            payload[f"{field}_minor"] = amount_to_minor(row.get(field))
        for field in numeric_fields:
            payload[field] = number(row.get(field))
        for field in ("free_shipping", "store_verified", "seller_verified"):
            payload[field] = optional_boolean(row.get(field))
        payload.update(
            {
                "consecutive_failures": integer(row.get("consecutive_failures")),
                "active": boolean(row.get("active")),
                "last_checked_at": dt(row.get("last_checked_at")),
                "last_success_at": dt(row.get("last_success_at")),
                "created_at": dt(row.get("created_at")),
                "updated_at": dt(row.get("updated_at")),
                "canonical_name": product.get("canonical_name"),
                "section": product.get("section"),
                "product_type": product.get("product_type"),
                "brand": product.get("brand"),
                "model": product.get("model"),
                "variant_name": product.get("variant_name"),
                "ram_gb": product.get("ram_gb"),
                "storage_gb": product.get("storage_gb"),
                "color": product.get("color"),
                "store_name": store.get("name"),
                "store_base_url": store.get("base_url"),
                "raw_payload": {},
                "price_fingerprint": None,
            }
        )
        yield row["offer_key"], payload


def discovery_documents(rows: list[dict[str, str]], mapping_by_offer: dict[str, str]):
    for row in rows:
        payload: dict[str, Any] = {
            **row,
            "mapping_id": mapping_by_offer.get(row["cash_offer_key"]),
            "active": boolean(row.get("active")),
            "evidence_verified_at": dt(row.get("evidence_verified_at")),
            "created_at": dt(row.get("created_at")),
            "updated_at": dt(row.get("updated_at")),
            "consecutive_failures": 0,
        }
        yield row["cash_offer_key"], {
            key: null(value) for key, value in payload.items()
        }


def bootstrap(repository: FirestoreRepository, *, rebuild_ready: bool = True) -> dict[str, int]:
    product_rows = read_rows("product_variants.csv")
    store_rows = read_rows("stores.csv")
    connector_rows = read_rows("connector_configs.csv")
    mapping_rows = read_rows("store_product_mappings.csv")
    operational_mapping_ids = {
        row["mapping_id"]
        for row in read_rows("operational_mapping_ids.csv")
        if row.get("mapping_id")
    }
    cash_rows = read_rows("current_cash_offers.csv")
    discovery_rows = read_rows("installment_discovery_tasks.csv")
    alias_rows = read_rows("variant_aliases.csv")

    products = {
        doc_id: payload for doc_id, payload in product_documents(product_rows)
    }
    stores = {doc_id: payload for doc_id, payload in store_documents(store_rows)}
    connectors = {
        doc_id: payload for doc_id, payload in connector_documents(connector_rows)
    }
    mappings = list(
        mapping_documents(
            mapping_rows,
            products,
            stores,
            connectors,
            operational_mapping_ids,
        )
    )

    counts = {
        "products": write_documents(
            repository, "product_variants", products.items()
        ),
        "stores": write_documents(repository, "stores", stores.items()),
        "connectors": write_documents(
            repository, "connector_configs", connectors.items()
        ),
        "aliases": write_documents(
            repository,
            "variant_aliases",
            (
                (
                    row["alias_variant_id"],
                    {
                        "alias_variant_id": row["alias_variant_id"],
                        "canonical_variant_id": row["canonical_variant_id"],
                        "reason": null(row.get("reason")),
                    },
                )
                for row in alias_rows
            ),
        ),
    }

    mapping_ids = existing_ids(repository, "mappings")
    missing_mappings = [
        (doc_id, payload)
        for doc_id, payload in mappings
        if _safe_doc_id(doc_id) not in mapping_ids
    ]
    counts["new_mappings"] = write_documents(
        repository,
        "mappings",
        missing_mappings,
        merge=False,
    )
    dynamic_mapping_fields = {
        "created_at",
        "updated_at",
        "metadata",
        "title_as_seen",
        "match_method",
        "match_confidence",
        "review_status",
        "direct_product_url",
        "last_discovered_at",
    }
    existing_mapping_updates = [
        (
            doc_id,
            {
                key: value
                for key, value in payload.items()
                if key not in dynamic_mapping_fields
            },
        )
        for doc_id, payload in mappings
        if _safe_doc_id(doc_id) in mapping_ids
    ]
    counts["existing_mapping_static_updates"] = write_documents(
        repository,
        "mappings",
        existing_mapping_updates,
    )
    counts["mappings"] = len(mappings)

    current_ids = existing_ids(repository, "cash_offers")
    cash_documents = list(cash_offer_documents(cash_rows, products, stores))
    missing_cash = [
        (doc_id, payload)
        for doc_id, payload in cash_documents
        if _safe_doc_id(doc_id) not in current_ids
    ]
    counts["new_cash_offers"] = write_documents(
        repository, "cash_offers", missing_cash, merge=False
    )
    static_cash_updates = []
    static_fields = {
        "offer_id",
        "offer_key",
        "mapping_id",
        "variant_id",
        "store_id",
        "seller_id",
        "seller_name",
        "currency",
        "delivery_region",
        "canonical_name",
        "section",
        "product_type",
        "brand",
        "model",
        "variant_name",
        "ram_gb",
        "storage_gb",
        "color",
        "store_name",
        "store_base_url",
    }
    for doc_id, payload in cash_documents:
        if _safe_doc_id(doc_id) in current_ids:
            static_cash_updates.append(
                (doc_id, {key: payload.get(key) for key in static_fields})
            )
    counts["existing_cash_static_updates"] = write_documents(
        repository, "cash_offers", static_cash_updates
    )

    mapping_by_offer = {
        row["offer_key"]: row["mapping_id"] for row in mapping_rows
    }
    discovery_ids = existing_ids(repository, "installment_discovery")
    all_discovery_documents = list(
        discovery_documents(discovery_rows, mapping_by_offer)
    )
    new_discovery_documents = [
        (doc_id, payload)
        for doc_id, payload in all_discovery_documents
        if _safe_doc_id(doc_id) not in discovery_ids
    ]
    counts["new_installment_discovery"] = write_documents(
        repository,
        "installment_discovery",
        new_discovery_documents,
        merge=False,
    )
    static_discovery_fields = {
        "task_id",
        "cash_offer_key",
        "mapping_id",
        "variant_id",
        "store_id",
        "seller_id",
        "source_url",
        "url_type",
        "title_as_seen",
        "active",
        "evidence_verified_at",
    }
    existing_discovery_updates = [
        (
            doc_id,
            {
                key: payload.get(key)
                for key in static_discovery_fields
            },
        )
        for doc_id, payload in all_discovery_documents
        if _safe_doc_id(doc_id) in discovery_ids
    ]
    counts["existing_installment_discovery_static_updates"] = write_documents(
        repository,
        "installment_discovery",
        existing_discovery_updates,
    )

    operational_mappings = [
        row
        for row in mapping_rows
        if boolean(row.get("active"))
        and row["mapping_id"] in operational_mapping_ids
    ]
    connected_stores = {row["store_id"] for row in operational_mappings}
    repository.set_registry_stats(
        products=len(product_rows),
        registry_stores=len(store_rows),
        active_stores=sum(boolean(row.get("active")) for row in store_rows),
        connected_stores=len(connected_stores),
        active_mappings=len(operational_mappings),
    )
    counts["catalog_shards"] = repository.rebuild_catalog_index()
    counts["comparison_docs"] = (
        repository.rebuild_all_comparisons() if rebuild_ready else 0
    )
    if rebuild_ready:
        repository._rebuild_system_stats()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Idempotently seed Sa3arly Firestore from the canonical CSVs"
    )
    parser.add_argument(
        "--skip-ready-docs",
        action="store_true",
        help="Skip catalog/comparison materialization (not recommended for production)",
    )
    args = parser.parse_args()
    repository = FirestoreRepository()
    counts = bootstrap(repository, rebuild_ready=not args.skip_ready_docs)
    print(json.dumps(counts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
