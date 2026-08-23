from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.settings import get_settings

REFRESH_HOURS_CAIRO = (10, 20)
CATALOG_DISCOVERY_HOUR_CAIRO = 2
CATALOG_DISCOVERY_MINUTE_CAIRO = 30


def next_refresh_at(now: datetime | None = None) -> datetime:
    """Return the next approved twice-daily Cairo price-refresh slot."""

    zone = ZoneInfo(get_settings().scheduler_timezone)
    local_now = now.astimezone(zone) if now else datetime.now(zone)
    for hour in REFRESH_HOURS_CAIRO:
        candidate = local_now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate > local_now:
            return candidate
    tomorrow = local_now + timedelta(days=1)
    return tomorrow.replace(
        hour=REFRESH_HOURS_CAIRO[0], minute=0, second=0, microsecond=0
    )


def refresh_slot_at(value: datetime | None = None) -> datetime:
    """Return the most recent approved slot as a stable UTC idempotency key."""

    zone = ZoneInfo(get_settings().scheduler_timezone)
    local = value.astimezone(zone) if value else datetime.now(zone)
    for hour in reversed(REFRESH_HOURS_CAIRO):
        candidate = local.replace(hour=hour, minute=0, second=0, microsecond=0)
        if candidate <= local:
            return candidate.astimezone(UTC)
    yesterday = local - timedelta(days=1)
    return yesterday.replace(
        hour=REFRESH_HOURS_CAIRO[-1], minute=0, second=0, microsecond=0
    ).astimezone(UTC)


def catalog_discovery_slot_at(value: datetime | None = None) -> datetime:
    """Return the latest 02:30 Cairo catalog slot as an idempotency key."""

    zone = ZoneInfo(get_settings().scheduler_timezone)
    local = value.astimezone(zone) if value else datetime.now(zone)
    candidate = local.replace(
        hour=CATALOG_DISCOVERY_HOUR_CAIRO,
        minute=CATALOG_DISCOVERY_MINUTE_CAIRO,
        second=0,
        microsecond=0,
    )
    if candidate > local:
        candidate -= timedelta(days=1)
    return candidate.astimezone(UTC)
