from datetime import UTC, datetime

from app.schedule import catalog_discovery_slot_at, next_refresh_at, refresh_slot_at


def test_next_refresh_uses_next_twice_daily_cairo_slot():
    now = datetime(2026, 7, 30, 8, 30, tzinfo=UTC)  # 11:30 Cairo
    next_slot = next_refresh_at(now)
    assert next_slot.day == 30
    assert next_slot.hour == 20
    assert next_slot.minute == 0


def test_retry_uses_same_cadence_slot_between_approved_runs():
    first = refresh_slot_at(datetime(2026, 7, 30, 10, 1, tzinfo=UTC))
    retry = refresh_slot_at(datetime(2026, 7, 30, 16, 59, tzinfo=UTC))
    assert first == retry


def test_20_cairo_creates_a_new_idempotency_slot():
    first = refresh_slot_at(datetime(2026, 7, 30, 16, 59, tzinfo=UTC))
    second = refresh_slot_at(datetime(2026, 7, 30, 17, 0, tzinfo=UTC))
    assert first != second


def test_catalog_slot_is_latest_0230_cairo():
    before = catalog_discovery_slot_at(datetime(2026, 7, 30, 22, 0, tzinfo=UTC))
    after = catalog_discovery_slot_at(datetime(2026, 7, 30, 23, 31, tzinfo=UTC))
    assert before.day == 29
    assert before.hour == 23
    assert before.minute == 30
    assert after.day == 30
    assert after.hour == 23
    assert after.minute == 30
