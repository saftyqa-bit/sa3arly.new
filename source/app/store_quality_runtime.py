from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.store_quality import calculate_store_quality, get_store_quality


def refresh_store_quality_if_needed(
    store_id: str,
    *,
    max_age_hours: int = 6,
) -> bool:
    cached = get_store_quality(store_id, recalculate=False)
    metrics = cached.get("metrics") if cached else None
    calculated_at = metrics.get("calculated_at") if isinstance(metrics, dict) else None
    if calculated_at:
        timestamp = datetime.fromisoformat(str(calculated_at).replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        if datetime.now(UTC) - timestamp.astimezone(UTC) <= timedelta(
            hours=max(1, max_age_hours)
        ):
            return False
    calculate_store_quality(store_id)
    return True
