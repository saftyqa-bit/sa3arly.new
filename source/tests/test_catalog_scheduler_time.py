from datetime import UTC, datetime, timedelta

from app.routes_internal import _effective_catalog_schedule_time


def test_full_catalog_manual_run_ignores_stale_scheduler_time():
    now = datetime(2026, 8, 5, 22, 0, tzinfo=UTC)
    stale = now - timedelta(hours=3)

    assert (
        _effective_catalog_schedule_time(
            "catalog-discovery-nightly",
            stale,
            now=now,
        )
        is None
    )


def test_full_catalog_keeps_fresh_scheduler_time_for_idempotency():
    now = datetime(2026, 8, 5, 22, 0, tzinfo=UTC)
    fresh = now - timedelta(minutes=2)

    assert (
        _effective_catalog_schedule_time(
            "catalog-discovery-nightly",
            fresh,
            now=now,
        )
        == fresh
    )


def test_non_full_catalog_keeps_existing_slot_behavior():
    now = datetime(2026, 8, 5, 22, 0, tzinfo=UTC)
    stale = now - timedelta(hours=3)

    assert _effective_catalog_schedule_time("scheduler", stale, now=now) == stale
