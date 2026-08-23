from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from scripts import start_price_refresh_job


def test_job_uses_next_approved_slot_and_starts_refresh(monkeypatch, capsys) -> None:
    scheduled_at = datetime(2026, 8, 9, 17, 0, tzinfo=UTC)
    captured: dict = {}

    monkeypatch.delenv("PRICE_REFRESH_SCHEDULED_AT", raising=False)
    monkeypatch.setenv("PRICE_REFRESH_SLOT_MODE", "next")
    monkeypatch.setattr(start_price_refresh_job, "next_refresh_at", lambda: scheduled_at)

    def fake_create_refresh_run(trigger: str, scheduled_at: datetime) -> dict:
        captured.update(trigger=trigger, scheduled_at=scheduled_at)
        return {
            "run_id": "price-run-1",
            "status": "queued",
            "task_count": 3034,
            "duplicate": False,
        }

    monkeypatch.setattr(
        start_price_refresh_job,
        "create_refresh_run",
        fake_create_refresh_run,
    )

    start_price_refresh_job.main()

    assert captured == {
        "trigger": "manual-after-catalog-bootstrap",
        "scheduled_at": scheduled_at,
    }
    output = capsys.readouterr().out.splitlines()
    assert output[0] == "PRICE_REFRESH_START=SUCCESS"
    payload = json.loads(output[1].removeprefix("PRICE_REFRESH_RESULT="))
    assert payload["result"]["run_id"] == "price-run-1"


def test_job_accepts_an_idempotent_duplicate(monkeypatch) -> None:
    monkeypatch.setenv("PRICE_REFRESH_SCHEDULED_AT", "2026-08-09T17:00:00Z")
    monkeypatch.setattr(
        start_price_refresh_job,
        "create_refresh_run",
        lambda trigger, scheduled_at: {
            "run_id": "price-run-existing",
            "status": "running",
            "duplicate": True,
        },
    )

    start_price_refresh_job.main()


def test_job_accepts_an_already_completed_fallback_slot(monkeypatch) -> None:
    monkeypatch.setenv("PRICE_REFRESH_SCHEDULED_AT", "2026-08-09T17:00:00Z")
    monkeypatch.setattr(
        start_price_refresh_job,
        "create_refresh_run",
        lambda trigger, scheduled_at: {
            "run_id": "price-run-existing",
            "status": "completed_with_errors",
            "duplicate": True,
        },
    )

    start_price_refresh_job.main()


def test_scheduled_job_uses_the_current_time(monkeypatch) -> None:
    monkeypatch.delenv("PRICE_REFRESH_SCHEDULED_AT", raising=False)
    monkeypatch.setenv("PRICE_REFRESH_SLOT_MODE", "current")

    scheduled_at = start_price_refresh_job._scheduled_at()

    assert scheduled_at.tzinfo is UTC


def test_job_rejects_a_naive_scheduled_at(monkeypatch) -> None:
    monkeypatch.setenv("PRICE_REFRESH_SCHEDULED_AT", "2026-08-09T17:00:00")

    with pytest.raises(ValueError, match="must include a timezone"):
        start_price_refresh_job.main()


def test_job_records_pre_enqueue_failure_in_the_run_ledger(monkeypatch) -> None:
    scheduled_at = datetime(2026, 8, 9, 17, 0, tzinfo=UTC)
    recorded: dict = {}

    class FakeRepository:
        @staticmethod
        def create_or_get_run(run_slot, trigger):
            recorded.update(run_slot=run_slot, trigger=trigger)
            return {"run_id": "price-run-failed"}, False

        @staticmethod
        def mark_run_enqueue_failed(run_id, message, **kwargs):
            recorded.update(run_id=run_id, message=message, **kwargs)

    monkeypatch.setenv("PRICE_REFRESH_SCHEDULED_AT", scheduled_at.isoformat())
    monkeypatch.setattr(start_price_refresh_job, "repository", FakeRepository())
    monkeypatch.setattr(
        start_price_refresh_job,
        "refresh_slot_at",
        lambda value: scheduled_at,
    )
    monkeypatch.setattr(
        start_price_refresh_job,
        "create_refresh_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("invalid mapping")),
    )

    with pytest.raises(ValueError, match="invalid mapping"):
        start_price_refresh_job.main()

    assert recorded == {
        "run_slot": scheduled_at,
        "trigger": "manual-after-catalog-bootstrap",
        "run_id": "price-run-failed",
        "message": "ValueError: invalid mapping",
        "successfully_queued": 0,
        "planned_tasks": 0,
    }


def test_job_never_overwrites_a_terminal_run_when_control_fails(monkeypatch) -> None:
    scheduled_at = datetime(2026, 8, 9, 17, 0, tzinfo=UTC)

    class FakeRepository:
        marked = False

        @staticmethod
        def create_or_get_run(run_slot, trigger):
            return {
                "run_id": "price-run-complete",
                "status": "completed_with_errors",
                "completed_at": scheduled_at,
            }, False

        @classmethod
        def mark_run_enqueue_failed(cls, *args, **kwargs):
            cls.marked = True

    monkeypatch.setenv("PRICE_REFRESH_SCHEDULED_AT", scheduled_at.isoformat())
    monkeypatch.setattr(start_price_refresh_job, "repository", FakeRepository())
    monkeypatch.setattr(start_price_refresh_job, "refresh_slot_at", lambda value: value)
    monkeypatch.setattr(
        start_price_refresh_job,
        "create_refresh_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("late fallback")),
    )

    with pytest.raises(RuntimeError, match="late fallback"):
        start_price_refresh_job.main()

    assert FakeRepository.marked is False


def test_read_only_termination_log_does_not_mask_result(monkeypatch) -> None:
    class ReadOnlyLog:
        class Parent:
            @staticmethod
            def exists() -> bool:
                return True

        parent = Parent()

        @staticmethod
        def write_text(*args, **kwargs) -> None:
            raise PermissionError("read only")

    monkeypatch.setattr(start_price_refresh_job, "_TERMINATION_LOG", ReadOnlyLog())

    start_price_refresh_job._write_termination_message({"status": "success"})
