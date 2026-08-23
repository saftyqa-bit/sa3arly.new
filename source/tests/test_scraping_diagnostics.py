from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

import pytest

from app.schemas import MappingTarget, ScrapeGroupPayload
from app.scraping import engine as engine_module
from app.scraping.engine import ScrapeEngine, _source_url_error
from app.scraping.fetcher import FetchError
from app.settings import Settings


def target() -> MappingTarget:
    return MappingTarget(
        mapping_id="MAP-1",
        offer_id="OFFER-1",
        offer_key="VAR-1|STORE-1|STORE",
        variant_id="VAR-1",
        store_id="STORE-1",
        store_name="Store One",
        source_url="https://shop.example/product",
        canonical_name="Phone One",
    )


def payload() -> ScrapeGroupPayload:
    now = datetime(2026, 7, 31, tzinfo=UTC)
    return ScrapeGroupPayload(
        task_id="TASK-1",
        run_id="RUN-1",
        run_slot=now,
        scheduled_for=now,
        store_id="STORE-1",
        store_name="Store One",
        source_url="https://shop.example/product",
        allowed_hosts=["shop.example"],
        mappings=[target()],
    )


class RecordingRepository:
    class PriceAnomalyError(ValueError):
        pass

    def __init__(self):
        self.finished: dict | None = None
        self.deferred: tuple[str, int] | None = None

    def mark_mapping_failure(self, *args, **kwargs):
        return None

    def finish_task(self, task_id: str, **kwargs):
        self.finished = {"task_id": task_id, **kwargs}

    def defer_store_requests(self, store_id: str, delay: int):
        self.deferred = (store_id, delay)


def test_single_failure_reason_is_preserved_for_all_mappings():
    assert (
        _source_url_error(
            Counter({"product_match_failed": 3}),
            all_mappings_failed=True,
        )
        == "product_match_failed"
    )
    assert (
        _source_url_error(
            Counter({"product_match_failed": 2, "price_not_found": 1}),
            all_mappings_failed=True,
        )
        == "all_mappings_failed"
    )


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_delay"),
    [
        (FetchError("blocked by robots", code="robots_disallowed"), "failed", None),
        (
            FetchError(
                "limited",
                code="rate_limited",
                status_code=429,
                retry_after_seconds=120,
            ),
            "retryable_failed",
            120,
        ),
    ],
)
def test_only_retryable_fetch_failures_open_the_store_circuit(
    monkeypatch: pytest.MonkeyPatch,
    error: FetchError,
    expected_status: str,
    expected_delay: int | None,
):
    repository = RecordingRepository()
    monkeypatch.setattr(engine_module, "repository", repository)
    engine = ScrapeEngine()
    engine.settings = Settings(_env_file=None)
    monkeypatch.setattr(engine, "_load_document", lambda **kwargs: (_ for _ in ()).throw(error))

    result = engine._process_locked(payload())

    assert result["status"] == expected_status
    assert repository.deferred == (
        ("STORE-1", expected_delay) if expected_delay is not None else None
    )


def test_unhandled_programming_errors_are_terminal_not_503_retries(
    monkeypatch: pytest.MonkeyPatch,
):
    repository = RecordingRepository()
    monkeypatch.setattr(engine_module, "repository", repository)
    engine = ScrapeEngine()
    monkeypatch.setattr(
        engine,
        "_load_document",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("bad parser state")),
    )

    result = engine._process_locked(payload())

    assert result == {"task_id": "TASK-1", "status": "failed", "error": "internal_valueerror"}
    assert repository.finished is not None
    assert repository.finished["status"] == "failed"
