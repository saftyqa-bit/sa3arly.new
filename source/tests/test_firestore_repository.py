from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.firestore_repository import (
    FirestoreRepository,
    PriceAnomalyError,
    _bounded_payload,
    _bounded_raw_payload,
    amount_to_minor,
    minor_to_amount,
)
from app.schemas import CashOfferExtract, InstallmentPlanExtract, MappingTarget, ScrapeGroupPayload
from app.scraping.engine import (
    PARSED_PAGE_CACHE_SCHEMA_VERSION,
    _cache_payload_reusable,
)
from app.settings import Settings
from tests.fake_firestore import FakeFirestoreClient


def settings(**overrides) -> Settings:
    values = {
        "persistence_backend": "firestore",
        "firestore_project_id": "test-project",
        "tasks_mode": "inline",
        "freshness_minutes": 75,
        "stale_after_minutes": 150,
        "refresh_interval_minutes": 60,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def repository(**overrides) -> FirestoreRepository:
    return FirestoreRepository(
        client=FakeFirestoreClient(), settings=settings(**overrides)
    )


def target() -> MappingTarget:
    return MappingTarget(
        mapping_id="MAP-1",
        offer_id="CASH-1",
        offer_key="VAR-1|STORE-1|STORE",
        variant_id="VAR-1",
        store_id="STORE-1",
        store_name="Store One",
        store_base_url="https://shop.example",
        source_url="https://shop.example/product",
        canonical_name="Phone One 256GB",
        section="Phones",
        product_type="Smartphone",
        brand="Example",
        model="Phone One",
        storage_gb=256,
    )


def seed_product(repo: FirestoreRepository) -> None:
    repo._col("product_variants").document("VAR-1").set(
        {
            "variant_id": "VAR-1",
            "canonical_name": "Phone One 256GB",
            "section": "Phones",
            "product_type": "Smartphone",
            "brand": "Example",
            "model": "Phone One",
            "variant_name": "256GB",
            "storage_gb": 256,
        }
    )


def cash(price: float, *, shipping: float | None = None) -> CashOfferExtract:
    return CashOfferExtract(
        currency="EGP",
        cash_price=price,
        shipping_cost=shipping,
        availability="available",
        source_url="https://shop.example/product",
        source_method="jsonld",
    )


def test_money_is_stored_as_integer_piastres():
    assert amount_to_minor("1234.505") == 123451
    assert amount_to_minor(0) == 0
    assert minor_to_amount(123451) == 1234.51


def test_run_status_paginates_tasks_without_overlap():
    repo = repository()
    run_id = "RUN-PAGE"
    scheduled_for = datetime(2026, 8, 1, tzinfo=UTC)
    repo._run_ref(run_id).set(
        {
            "run_id": run_id,
            "status": "running",
            "queued_task_count": 7,
        }
    )
    for index in range(7):
        repo._col("scrape_task_runs").document(f"TASK-{index}").set(
            {
                "external_task_id": f"TASK-{index}",
                "run_id": run_id,
                "store_id": f"STORE-{index % 2}",
                "source_url": f"https://shop.example/product/{index}",
                "status": "queued",
                "scheduled_for": scheduled_for,
            }
        )

    first = repo.get_run(run_id, task_limit=3, task_offset=0)
    second = repo.get_run(run_id, task_limit=3, task_offset=3)
    final = repo.get_run(run_id, task_limit=3, task_offset=6)

    assert first is not None
    assert second is not None
    assert final is not None
    ids = [
        item["external_task_id"]
        for page in (first, second, final)
        for item in page["tasks"]
    ]
    assert len(ids) == 7
    assert len(set(ids)) == 7
    assert first["pagination"] == {
        "limit": 3,
        "offset": 0,
        "returned_task_rows": 3,
        "total_task_rows": 7,
        "has_more": True,
    }
    assert final["pagination"] == {
        "limit": 3,
        "offset": 6,
        "returned_task_rows": 1,
        "total_task_rows": 7,
        "has_more": False,
    }


@pytest.mark.parametrize(
    ("task_limit", "task_offset"),
    [(0, 0), (501, 0), (500, -1)],
)
def test_run_status_rejects_invalid_pagination(task_limit: int, task_offset: int):
    repo = repository()

    with pytest.raises(ValueError):
        repo.get_run(
            "RUN-PAGE",
            task_limit=task_limit,
            task_offset=task_offset,
        )


def test_store_circuit_is_durable_and_long_waits_fail_fast():
    repo = repository()
    repo.defer_store_requests("STORE-1", 120)

    wait = repo.reserve_store_request_slot(
        "STORE-1",
        10,
        max_wait_seconds=15,
    )

    assert 100 <= wait <= 120
    state = repo._snapshot(repo._col("store_rate_limits").document("STORE-1").get())
    assert state is not None
    assert state["circuit_open"] is True


def test_page_cache_payload_is_bounded_without_dropping_document_shape():
    value = {
        "visible_text": "x" * 900_000,
        "candidates": [{"title": "phone", "raw": "y" * 2000}] * 1000,
        "links": [{"url": "https://example.com", "raw": "z" * 2000}] * 2000,
        "raw_summary": {"large": "q" * 100_000},
    }
    bounded = _bounded_payload(value, 700_000)
    assert len(str(bounded).encode("utf-8")) < 800_000
    assert "visible_text" in bounded
    assert bounded["cache_truncated"] is True
    assert bounded["raw_summary"]["cache_truncated"] is True


def test_oversized_raw_evidence_is_replaced_with_a_bounded_audit_preview():
    bounded = _bounded_raw_payload(
        {"nested": {"payload": "x" * 1_000_000}},
        max_bytes=100_000,
    )
    assert bounded["raw_truncated"] is True
    assert bounded["original_bytes"] > 1_000_000
    assert len(str(bounded).encode("utf-8")) < 100_000


def test_small_deep_raw_evidence_is_flattened_before_firestore_write():
    value: dict[str, object] = {"leaf": "safe"}
    for index in range(40):
        value = {f"level_{index}": value}

    bounded = _bounded_raw_payload(value)
    assert bounded["raw_truncated"] is False
    assert isinstance(bounded["json"], str)
    assert all(not isinstance(item, (dict, list)) for item in bounded.values())


def test_page_cache_flattens_nested_candidate_and_summary_evidence():
    deep: dict[str, object] = {"leaf": "safe"}
    for index in range(40):
        deep = {f"level_{index}": deep}

    bounded = _bounded_payload(
        {
            "candidates": [{"title": "Phone", "raw": deep}],
            "links": [],
            "raw_summary": deep,
        },
        700_000,
    )
    assert isinstance(bounded["candidates"][0]["raw"]["json"], str)
    assert isinstance(bounded["raw_summary"]["json"], str)


def test_truncated_page_cache_is_not_reused_after_http_304():
    current = {
        "schema_version": PARSED_PAGE_CACHE_SCHEMA_VERSION,
        "candidates": [],
        "raw_summary": {},
    }
    assert _cache_payload_reusable(current) is True
    assert _cache_payload_reusable({"candidates": [], "raw_summary": {}}) is False
    assert _cache_payload_reusable({**current, "schema_version": 1}) is False
    assert _cache_payload_reusable({**current, "cache_truncated": True}) is False
    assert (
        _cache_payload_reusable(
            {
                **current,
                "raw_summary": {"cache_truncated": True},
            }
        )
        is False
    )


def test_run_and_task_transitions_are_idempotent():
    repo = repository()
    slot = datetime(2026, 7, 30, 20, tzinfo=UTC)
    run, created = repo.create_or_get_run(slot, "test")
    duplicate, duplicate_created = repo.create_or_get_run(slot, "retry")
    assert created is True
    assert duplicate_created is False
    assert duplicate["run_id"] == run["run_id"]

    repo.mark_run_enqueuing(
        run["run_id"],
        mapping_count=1,
        url_group_count=1,
        queued_task_count=1,
    )
    payload = ScrapeGroupPayload(
        task_id="TASK-1",
        run_id=run["run_id"],
        run_slot=slot,
        scheduled_for=slot,
        store_id="STORE-1",
        store_name="Store",
        source_url="https://shop.example/product",
        mapping_ids=["MAP-1"],
    )
    repo.register_task_run(payload)
    repo.register_task_run(payload)
    repo.mark_run_enqueue_complete(run["run_id"])
    assert repo.start_task("TASK-1") == "claimed"
    assert repo.start_task("TASK-1") == "running"

    repo.finish_task("TASK-1", status="success", cash_updates=1)
    repo.finish_task("TASK-1", status="success", cash_updates=1)
    repo.finish_task(
        "TASK-1",
        status="retryable_failed",
        error_code="late_retry",
    )
    status = repo.get_run(run["run_id"])
    assert status is not None
    assert status["run"]["completed_task_count"] == 1
    assert status["run"]["successful_task_count"] == 1
    assert status["run"]["cash_updates"] == 1
    assert status["run"]["status"] == "completed"
    task_row = repo._snapshot(
        repo._col("scrape_task_runs").document("TASK-1").get()
    )
    assert task_row is not None
    assert task_row["status"] == "success"
    assert task_row["terminal"] is True


def test_cash_history_is_written_only_when_price_snapshot_changes():
    repo = repository()
    seed_product(repo)
    changed = repo.upsert_cash_offer(
        target(), cash(10_000), run_id="RUN-1", connector_version="test-v1"
    )
    repo.upsert_installment_plans(
        target(), [], run_id="RUN-1", connector_version="test-v1"
    )
    assert changed is True
    assert len(list(repo._col("cash_offer_history").stream())) == 1

    changed = repo.upsert_cash_offer(
        target(), cash(10_000), run_id="RUN-2", connector_version="test-v1"
    )
    repo.upsert_installment_plans(
        target(), [], run_id="RUN-2", connector_version="test-v1"
    )
    assert changed is False
    assert len(list(repo._col("cash_offer_history").stream())) == 1

    changed = repo.upsert_cash_offer(
        target(), cash(10_500), run_id="RUN-3", connector_version="test-v1"
    )
    repo.upsert_installment_plans(
        target(), [], run_id="RUN-3", connector_version="test-v1"
    )
    assert changed is True
    assert len(list(repo._col("cash_offer_history").stream())) == 2
    comparison = repo.get_product_comparison("VAR-1")
    assert comparison is not None
    assert comparison["cash_offers"][0]["cash_price"] == 10_500


def test_price_anomaly_is_rejected_transactionally():
    repo = repository()
    seed_product(repo)
    repo.upsert_cash_offer(
        target(), cash(10_000), run_id="RUN-1", connector_version="test-v1"
    )
    with pytest.raises(PriceAnomalyError):
        repo.upsert_cash_offer(
            target(), cash(100), run_id="RUN-2", connector_version="test-v1"
        )
    current = repo._snapshot(repo._col("cash_offers").document("VAR-1|STORE-1|STORE").get())
    assert current is not None
    assert current["cash_price_minor"] == 1_000_000


def test_external_offer_and_mapping_strings_are_bounded_before_firestore_write():
    repo = repository()
    seed_product(repo)
    repo._col("mappings").document("MAP-1").set(
        {
            "mapping_id": "MAP-1",
            "source_url": "https://shop.example/category",
            "metadata": {},
        }
    )
    huge = "ع" * 600_000

    repo.update_mapping_direct_url(
        "MAP-1",
        "https://shop.example/product",
        huge,
        90,
        prefer_for_scrape=True,
    )
    mapping = repo._col("mappings").document("MAP-1").get().to_dict()
    assert mapping is not None
    assert len(mapping["title_as_seen"].encode("utf-8")) <= 4_000

    result = CashOfferExtract(
        currency="EGP",
        cash_price=10_000,
        seller_name=huge,
        source_url="https://shop.example/product",
        source_method="jsonld",
    )
    repo.upsert_cash_offer(
        target(), result, run_id="RUN-1", connector_version="test-v1"
    )
    offer = (
        repo._col("cash_offers")
        .document("VAR-1|STORE-1|STORE")
        .get()
        .to_dict()
    )
    assert offer is not None
    assert len(offer["seller_name"].encode("utf-8")) <= 4_000
    assert len(json.dumps(offer, default=str, ensure_ascii=False).encode("utf-8")) < 100_000

    repo.upsert_installment_plans(
        target(),
        [
            InstallmentPlanExtract(
                provider_name=huge,
                months=12,
                total_published=12_000,
                source_url="https://shop.example/product",
                completeness="complete",
            )
        ],
        run_id="RUN-1",
        connector_version="test-v1",
    )
    plan = next(repo._col("installment_plans").stream()).to_dict()
    assert plan is not None
    assert len(plan["provider_name"].encode("utf-8")) <= 4_000
    assert len(json.dumps(plan, default=str, ensure_ascii=False).encode("utf-8")) < 150_000


def test_bulk_comparison_rebuild_never_overwrites_newer_materialization():
    repo = repository()
    seed_product(repo)
    future = datetime.now(UTC) + timedelta(hours=1)
    repo._col("comparison_docs").document("VAR-1").set(
        {
            "variant_id": "VAR-1",
            "source_observed_at": future,
            "marker": "newer",
        }
    )
    repo._col("comparison_summaries").document("VAR-1").set(
        {
            "variant_id": "VAR-1",
            "source_observed_at": future,
            "marker": "newer",
        }
    )

    assert repo.rebuild_all_comparisons() == 0
    assert (
        repo._col("comparison_docs").document("VAR-1").get().to_dict()["marker"]
        == "newer"
    )
    assert (
        repo._col("comparison_summaries")
        .document("VAR-1")
        .get()
        .to_dict()["marker"]
        == "newer"
    )


def test_bulk_comparison_rebuild_chunks_writes_below_request_budget():
    repo = repository()
    for index in range(10):
        variant_id = f"VAR-{index}"
        repo._col("product_variants").document(variant_id).set(
            {
                "variant_id": variant_id,
                "canonical_name": f"Phone {index}",
            }
        )
        repo._col("cash_offers").document(f"OFFER-{index}").set(
            {
                "offer_key": f"OFFER-{index}",
                "variant_id": variant_id,
                "store_id": "STORE-1",
                "large_but_valid_field": "x" * 650_000,
            }
        )

    assert repo.rebuild_all_comparisons() == 10
    assert max(repo.client.transaction_write_bytes) < 7_000_000


def test_ready_comparison_preserves_unknown_shipping_and_plan_ranking_contract():
    repo = repository()
    seed_product(repo)
    repo.upsert_cash_offer(
        target(), cash(10_000, shipping=None), run_id="RUN-1", connector_version="test-v1"
    )
    partial = InstallmentPlanExtract(
        provider_name="Bank",
        months=12,
        periodic_payment=900,
        cash_price_at_observation=10_000,
        source_url="https://shop.example/product",
        completeness="partial",
    )
    repo.upsert_installment_plans(
        target(), [partial], run_id="RUN-1", connector_version="test-v1"
    )
    comparison = repo.get_product_comparison("VAR-1")
    assert comparison is not None
    offer = comparison["cash_offers"][0]
    plan = comparison["installment_plans"][0]
    assert offer["shipping_cost_known"] is False
    assert offer["comparable_total"] is None
    assert plan["eligible_for_ranking"] is False
    assert plan["currency"] == "EGP"
    assert plan["store_name"] == "Store One"
    assert plan["canonical_name"] == "Phone One 256GB"


def test_non_egp_installment_plan_is_never_ranked():
    repo = repository()
    seed_product(repo)
    usd_cash = CashOfferExtract(
        currency="USD",
        cash_price=200,
        availability="available",
        source_url="https://shop.example/product",
        source_method="jsonld",
    )
    repo.upsert_cash_offer(
        target(), usd_cash, run_id="RUN-1", connector_version="test-v1"
    )
    plan = InstallmentPlanExtract(
        provider_name="Bank",
        months=12,
        periodic_payment=900,
        total_published=10_800,
        cash_price_at_observation=10_000,
        source_url="https://shop.example/product",
        completeness="complete",
    )
    repo.upsert_installment_plans(
        target(), [plan], run_id="RUN-1", connector_version="test-v1"
    )
    comparison = repo.get_product_comparison("VAR-1")
    assert comparison is not None
    assert comparison["installment_plans"][0]["currency"] == "USD"
    assert comparison["installment_plans"][0]["eligible_for_ranking"] is False


def test_status_contains_hourly_schedule_fields():
    repo = repository()
    repo.set_registry_stats(
        products=2471,
        registry_stores=216,
        active_stores=216,
        connected_stores=14,
        active_mappings=2360,
    )
    status = repo.system_stats()
    assert status["refresh_interval_minutes"] == 60
    assert status["next_update_at"]
    assert status["latest_installment_update"] is None
