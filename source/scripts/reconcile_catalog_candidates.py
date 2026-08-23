from __future__ import annotations

import json
import os
from typing import Any

from app.repository_provider import repository
from app.settings import get_settings


def reconcile_all() -> dict[str, Any]:
    if get_settings().persistence_backend != "postgres":
        raise RuntimeError("Catalog reconciliation requires the PostgreSQL backend")
    batch_size = max(100, min(int(os.environ.get("CATALOG_RECONCILE_BATCH_SIZE", "1000")), 5000))
    after_candidate_id: str | None = None
    after_import_item_id: str | None = None
    totals = {
        "processed": 0,
        "matched": 0,
        "mappings_created": 0,
        "mappings_refreshed": 0,
        "import_observations_processed": 0,
        "catalog_entities_linked": 0,
        "source_variants_created": 0,
        "source_prices_published": 0,
        "superseded_duplicate_tasks": (
            repository.reconcile_overlapping_catalog_discovery_runs()
        ),
    }
    methods: dict[str, int] = {}
    while True:
        result = repository.reconcile_catalog_import_observations_batch(
            after_item_id=after_import_item_id,
            limit=min(batch_size, 2000),
        )
        totals["import_observations_processed"] += int(result.get("processed") or 0)
        totals["catalog_entities_linked"] += int(result.get("entities_linked") or 0)
        totals["source_variants_created"] += int(result.get("variants_created") or 0)
        totals["mappings_created"] += int(result.get("mappings_created") or 0)
        totals["source_prices_published"] += int(result.get("prices_published") or 0)
        after_import_item_id = result.get("last_item_id")
        print(
            "CATALOG_IMPORT_RECONCILE_BATCH="
            + json.dumps(result, ensure_ascii=False, sort_keys=True)
        )
        if not result.get("has_more"):
            break
    while True:
        result = repository.reconcile_catalog_candidates_batch(
            after_candidate_id=after_candidate_id,
            limit=batch_size,
        )
        for key in totals:
            totals[key] += int(result.get(key) or 0)
        for method, count in (result.get("methods") or {}).items():
            methods[str(method)] = methods.get(str(method), 0) + int(count or 0)
        after_candidate_id = result.get("last_candidate_id")
        print("CATALOG_RECONCILE_BATCH=" + json.dumps(result, ensure_ascii=False, sort_keys=True))
        if not result.get("has_more"):
            break
    totals["sources_reset_due"] = repository.reset_catalog_discovery_sources_due()
    totals["methods"] = methods
    return totals


def main() -> None:
    totals = reconcile_all()
    print("CATALOG_RECONCILIATION=SUCCESS")
    print("CATALOG_RECONCILIATION_TOTALS=" + json.dumps(totals, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
