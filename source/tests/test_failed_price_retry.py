from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

from app import hourly
from app.settings import Settings
from scripts import retry_failed_price_tasks_job


def _mapping(mapping_id: str) -> dict:
    return {
        "mapping_id": mapping_id,
        "store_id": "EG-029",
        "store_name": "Feel22 Egypt",
        "source_url": f"https://eg.feel22.com/products/{mapping_id}",
        "effective_source_url": f"https://eg.feel22.com/products/{mapping_id}",
        "url_type": "direct",
        "effective_url_type": "direct",
        "allowed_hosts": ["eg.feel22.com"],
        "connector_mode": "auto",
        "connector_version": "generic-v1",
        "connector_config": {},
        "respect_robots": True,
        "browser_required": False,
        "requests_per_minute": 12,
        "max_concurrency": 2,
    }


def test_retry_run_loads_only_failed_source_groups(monkeypatch) -> None:
    source_run_id = "7cb5660e-a158-41d0-b16a-285202f898fd"
    slot = datetime(2026, 8, 12, 10, 15, tzinfo=UTC)

    class Repository:
        def __init__(self):
            self.repaired = None
            self.failed_source = None
            self.payloads = []
            self.metadata = None

        def reconcile_stale_runs(self, _minutes):
            return 0

        def repair_terminal_price_run(self, run_id):
            self.repaired = run_id

        def create_or_get_run(self, run_slot, trigger):
            assert run_slot == slot
            assert trigger == "failed-only-retry"
            return {
                "run_id": "00000000-0000-0000-0000-000000000002",
                "status": "created",
                "metadata": {},
            }, True

        def load_failed_mapping_rows(self, run_id):
            self.failed_source = run_id
            return [_mapping("MAP-1"), _mapping("MAP-2")]

        def mark_run_enqueuing(self, _run_id, **kwargs):
            self.metadata = kwargs["metadata"]

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
        lambda: Settings(_env_file=None, tasks_mode="cloud"),
    )

    result = hourly.create_refresh_run(
        "failed-only-retry",
        scheduled_at=slot,
        retry_source_run_id=source_run_id,
    )

    assert repository.repaired == source_run_id
    assert repository.failed_source == source_run_id
    assert len(repository.payloads) == 2
    assert repository.metadata["retry_source_run_id"] == source_run_id
    assert repository.metadata["retry_failed_tasks_only"] is True
    assert result["retry_source_run_id"] == source_run_id
    assert result["task_count"] == 2


def test_retry_job_passes_the_source_run_to_the_planner(monkeypatch, capsys) -> None:
    source_run_id = "7cb5660e-a158-41d0-b16a-285202f898fd"
    captured = {}

    def create(trigger, scheduled_at, *, retry_source_run_id):
        captured.update(
            trigger=trigger,
            scheduled_at=scheduled_at,
            retry_source_run_id=retry_source_run_id,
        )
        return {"run_id": "retry-run", "status": "queued", "task_count": 12}

    monkeypatch.setattr(retry_failed_price_tasks_job, "create_refresh_run", create)
    monkeypatch.setattr(sys, "argv", ["retry_failed_price_tasks_job", source_run_id])

    retry_failed_price_tasks_job.main()

    assert captured["trigger"] == "failed-only-retry"
    assert captured["retry_source_run_id"] == source_run_id
    assert captured["scheduled_at"].tzinfo is UTC
    output = capsys.readouterr().out.strip()
    payload = json.loads(output.removeprefix("PRICE_FAILED_RETRY="))
    assert payload["source_run_id"] == source_run_id
