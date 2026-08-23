from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from app import hourly
from app.settings import Settings


def group_key(url_type: str) -> tuple:
    return (
        "STORE-1",
        "https://shop.example/product",
        url_type,
        ("shop.example",),
        "auto",
        "generic-v1",
        True,
        False,
        10,
        1,
    )


def mapping_row(mapping_id: str, url_type: str) -> dict:
    return {
        "mapping_id": mapping_id,
        "store_id": "STORE-1",
        "store_name": "Store One",
        "source_url": "https://shop.example/product",
        "effective_source_url": "https://shop.example/product",
        "url_type": url_type,
        "effective_url_type": url_type,
        "allowed_hosts": ["shop.example"],
        "connector_mode": "auto",
        "connector_version": "generic-v1",
        "connector_config": {},
        "respect_robots": True,
        "browser_required": False,
        "requests_per_minute": 10,
        "max_concurrency": 1,
    }


def test_repeated_legacy_identity_gets_full_group_suffix():
    slot = datetime(2026, 7, 31, 17, tzinfo=UTC)  # 20:00 Cairo approved slot
    first = hourly._task_id_for_group(slot, group_key("listing"), legacy_occurrence=0)
    second = hourly._task_id_for_group(slot, group_key("direct"), legacy_occurrence=1)

    legacy_hash = hashlib.sha1(
        f"{slot.isoformat()}|STORE-1|https://shop.example/product".encode()
    ).hexdigest()
    expected_legacy = f"{slot:%Y%m%d%H}-STORE-1-{legacy_hash[:16]}"

    assert first == expected_legacy
    assert second.startswith(expected_legacy + "-")
    assert second != first
    assert second == hourly._task_id_for_group(
        slot, group_key("direct"), legacy_occurrence=1
    )


def test_existing_unexecuted_run_can_be_repaired_once(
    monkeypatch: pytest.MonkeyPatch,
):
    slot = datetime(2026, 7, 31, 17, tzinfo=UTC)  # 20:00 Cairo approved slot

    class Repository:
        def __init__(self):
            self.metadata = None
            self.payloads = []
            self.completed = False

        def reconcile_stale_runs(self, _minutes):
            return 0

        def create_or_get_run(self, _run_slot, _trigger):
            return (
                {
                    "run_id": "ca67a2bc-e7c1-4249-8242-7d374eee69ed",
                    "status": "queued",
                    "completed_task_count": 0,
                    "successful_task_count": 0,
                    "failed_task_count": 0,
                    "metadata": {"enqueue_complete": True},
                },
                False,
            )

        def load_active_mapping_rows(self):
            return [
                mapping_row("MAP-2", "direct"),
                mapping_row("MAP-1", "listing"),
            ]

        def load_registered_task_identities(self, _run_id):
            legacy_id = hourly._legacy_task_id(slot, group_key("listing"))
            return {
                legacy_id: {
                    "source_url": "https://shop.example/product",
                    "url_type": "listing",
                }
            }

        def mark_run_enqueuing(self, _run_id, **kwargs):
            self.metadata = kwargs["metadata"]

        def register_task_run(self, payload):
            self.payloads.append(payload)

        def count_registered_tasks(self, _run_id):
            return len(self.payloads)

        def mark_run_enqueue_complete(self, _run_id):
            self.completed = True

        def mark_run_enqueue_failed(self, *args, **kwargs):
            raise AssertionError((args, kwargs))

    repository = Repository()

    class Enqueuer:
        def enqueue(self, payload):
            return payload.task_id

    monkeypatch.setattr(hourly, "repository", repository)
    monkeypatch.setattr(hourly, "TaskEnqueuer", Enqueuer)
    monkeypatch.setattr(
        hourly,
        "get_settings",
        lambda: Settings(_env_file=None, tasks_mode="cloud"),
    )

    result = hourly.create_refresh_run(
        hourly.TASK_NAME_COLLISION_REPAIR_TRIGGER,
        scheduled_at=slot,
    )

    task_ids = [payload.task_id for payload in repository.payloads]
    assert len(task_ids) == 2
    assert len(set(task_ids)) == 2
    assert repository.payloads[0].url_type == "listing"
    assert repository.payloads[1].url_type == "direct"
    assert task_ids[0] == hourly._legacy_task_id(slot, group_key("listing"))
    assert result["collision_repair"] is True
    assert result["task_id_collisions_disambiguated"] == 1
    assert result["registered_task_count"] == 2
    assert result["task_id_scheme"] == hourly.TASK_ID_SCHEME
    assert repository.metadata["collision_repair_requested"] is True
    assert repository.metadata["task_id_scheme"] == hourly.TASK_ID_SCHEME
    assert repository.completed is True


def test_normal_trigger_does_not_repair_queued_legacy_run(
    monkeypatch: pytest.MonkeyPatch,
):
    slot = datetime(2026, 7, 31, 17, tzinfo=UTC)  # 20:00 Cairo approved slot

    class Repository:
        def reconcile_stale_runs(self, _minutes):
            return 0

        def create_or_get_run(self, _run_slot, _trigger):
            return (
                {
                    "run_id": "ca67a2bc-e7c1-4249-8242-7d374eee69ed",
                    "status": "queued",
                    "completed_task_count": 0,
                    "successful_task_count": 0,
                    "failed_task_count": 0,
                    "metadata": {"enqueue_complete": True},
                },
                False,
            )

    monkeypatch.setattr(hourly, "repository", Repository())
    monkeypatch.setattr(
        hourly,
        "get_settings",
        lambda: Settings(_env_file=None, tasks_mode="cloud"),
    )

    result = hourly.create_refresh_run("manual", scheduled_at=slot)

    assert result["duplicate"] is True
    assert result["status"] == "queued"


def test_cloud_tasks_are_enqueued_in_fair_due_time_order(
    monkeypatch: pytest.MonkeyPatch,
):
    slot = datetime(2026, 7, 31, 17, tzinfo=UTC)

    def row(store_id: str, index: int) -> dict:
        value = mapping_row(f"{store_id}-{index}", "direct")
        value.update(
            {
                "store_id": store_id,
                "store_name": store_id,
                "source_url": f"https://shop.example/{store_id}/{index}",
                "effective_source_url": f"https://shop.example/{store_id}/{index}",
                "requests_per_minute": 1,
            }
        )
        return value

    class Repository:
        def __init__(self):
            self.payloads = []

        def reconcile_stale_runs(self, _minutes):
            return 0

        def create_or_get_run(self, _run_slot, _trigger):
            return (
                {
                    "run_id": "ca67a2bc-e7c1-4249-8242-7d374eee69ed",
                    "status": "created",
                    "metadata": {},
                },
                True,
            )

        def load_active_mapping_rows(self):
            return [
                row("STORE-A", 0),
                row("STORE-A", 1),
                row("STORE-A", 2),
                row("STORE-B", 0),
                row("STORE-B", 1),
                row("STORE-B", 2),
            ]

        def mark_run_enqueuing(self, _run_id, **_kwargs):
            return None

        def register_task_run(self, payload):
            self.payloads.append(payload)

        def count_registered_tasks(self, _run_id):
            return len(self.payloads)

        def mark_run_enqueue_complete(self, _run_id):
            return None

        def mark_run_enqueue_failed(self, *args, **kwargs):
            raise AssertionError((args, kwargs))

    repository = Repository()

    class Enqueuer:
        def enqueue(self, payload):
            return payload.task_id

    monkeypatch.setattr(hourly, "repository", repository)
    monkeypatch.setattr(hourly, "TaskEnqueuer", Enqueuer)
    monkeypatch.setattr(
        hourly,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            tasks_mode="cloud",
            task_stagger_seconds=10,
        ),
    )

    result = hourly.create_refresh_run("manual", scheduled_at=slot)

    stores = [payload.store_id for payload in repository.payloads]
    assert set(stores[:2]) == {"STORE-A", "STORE-B"}
    assert set(stores[2:4]) == {"STORE-A", "STORE-B"}
    assert result["staggered_over_seconds"] > 10
    assert result["staggered_over_seconds"] < result["scheduling_window_seconds"]


def test_price_run_refuses_a_store_that_cannot_fit_before_finalization(
    monkeypatch: pytest.MonkeyPatch,
):
    slot = datetime(2026, 7, 31, 17, tzinfo=UTC)

    class Repository:
        def __init__(self):
            self.failure = None

        def reconcile_stale_runs(self, _minutes):
            return 0

        def create_or_get_run(self, _run_slot, _trigger):
            return (
                {
                    "run_id": "ca67a2bc-e7c1-4249-8242-7d374eee69ed",
                    "status": "created",
                    "metadata": {},
                },
                True,
            )

        def load_active_mapping_rows(self):
            rows = []
            for index in range(4):
                value = mapping_row(f"MAP-{index}", "direct")
                value["source_url"] = f"https://shop.example/{index}"
                value["effective_source_url"] = value["source_url"]
                value["requests_per_minute"] = 1
                rows.append(value)
            return rows

        def mark_run_enqueue_failed(self, *args, **kwargs):
            self.failure = (args, kwargs)

    repository = Repository()
    monkeypatch.setattr(hourly, "repository", repository)
    monkeypatch.setattr(
        hourly,
        "get_settings",
        lambda: Settings(
            _env_file=None,
            tasks_mode="cloud",
            refresh_interval_minutes=4,
        ),
    )

    with pytest.raises(RuntimeError, match="cannot fit inside"):
        hourly.create_refresh_run("manual", scheduled_at=slot)

    assert repository.failure is not None
    assert repository.failure[1]["successfully_queued"] == 0
    assert repository.failure[1]["planned_tasks"] == 4
