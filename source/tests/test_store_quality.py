from datetime import UTC, datetime, timedelta

from app import store_quality_runtime
from app.store_quality import _percent_or_none


def test_store_quality_does_not_invent_scores_without_a_sample():
    assert _percent_or_none(0, 0) is None
    assert _percent_or_none(10, 0) is None


def test_store_quality_percentage_is_bounded():
    assert _percent_or_none(8, 10) == 80
    assert _percent_or_none(15, 10) == 100
    assert _percent_or_none(-1, 10) == 0


def test_recent_store_quality_is_reused(monkeypatch):
    calculated = []
    monkeypatch.setattr(
        store_quality_runtime,
        "get_store_quality",
        lambda store_id, recalculate=False: {
            "store": {"store_id": store_id},
            "metrics": {"calculated_at": datetime.now(UTC).isoformat()},
        },
    )
    monkeypatch.setattr(
        store_quality_runtime,
        "calculate_store_quality",
        lambda store_id: calculated.append(store_id),
    )
    assert not store_quality_runtime.refresh_store_quality_if_needed("store-a")
    assert calculated == []


def test_stale_store_quality_is_recalculated(monkeypatch):
    calculated = []
    monkeypatch.setattr(
        store_quality_runtime,
        "get_store_quality",
        lambda store_id, recalculate=False: {
            "store": {"store_id": store_id},
            "metrics": {
                "calculated_at": (
                    datetime.now(UTC) - timedelta(hours=7)
                ).isoformat()
            },
        },
    )
    monkeypatch.setattr(
        store_quality_runtime,
        "calculate_store_quality",
        lambda store_id: calculated.append(store_id),
    )
    assert store_quality_runtime.refresh_store_quality_if_needed("store-a")
    assert calculated == ["store-a"]
