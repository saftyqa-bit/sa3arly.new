from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

from app.hourly import create_refresh_run
from app.repository_provider import repository
from app.schedule import next_refresh_at, refresh_slot_at

_TERMINATION_LOG = Path("/dev/termination-log")
_TERMINATION_LIMIT = 4096
_TRIGGER = "manual-after-catalog-bootstrap"
_TERMINAL_RUN_STATUSES = {"completed", "completed_with_errors", "cancelled"}


def _scheduled_at() -> datetime:
    override = os.environ.get("PRICE_REFRESH_SCHEDULED_AT", "").strip()
    if not override:
        mode = os.environ.get("PRICE_REFRESH_SLOT_MODE", "current").strip().lower()
        if mode == "current":
            return datetime.now(UTC)
        if mode == "next":
            return next_refresh_at()
        raise ValueError("PRICE_REFRESH_SLOT_MODE must be 'current' or 'next'")

    parsed = datetime.fromisoformat(override.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("PRICE_REFRESH_SCHEDULED_AT must include a timezone")
    return parsed


def _write_termination_message(payload: dict) -> None:
    if not _TERMINATION_LOG.parent.exists():
        return
    message = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    try:
        _TERMINATION_LOG.write_text(message[-_TERMINATION_LIMIT:], encoding="utf-8")
    except OSError:
        # Cloud Run exposes this file as writable, while developer and CI Linux
        # hosts may expose a read-only /dev. Diagnostics must never mask the
        # refresh result or its original exception.
        return


def _record_control_failure(scheduled_at: datetime, exc: Exception) -> str | None:
    """Persist pre-enqueue failures in the same idempotent run ledger."""

    try:
        run_slot = refresh_slot_at(scheduled_at)
        run, created = repository.create_or_get_run(run_slot, _TRIGGER)
        message = f"{type(exc).__name__}: {exc}"[:2000]
        status = str(run.get("status") or "created")
        if created or (
            status in {"created", "enqueuing", "queued", "running"} and not run.get("completed_at")
        ):
            repository.mark_run_enqueue_failed(
                str(run["run_id"]),
                message,
                successfully_queued=0,
                planned_tasks=0,
            )
        return str(run["run_id"])
    except Exception:
        return None


def main() -> None:
    scheduled_at = None
    try:
        scheduled_at = _scheduled_at()
        result = create_refresh_run(
            _TRIGGER,
            scheduled_at=scheduled_at,
        )
        if not result.get("run_id"):
            raise RuntimeError("Price refresh did not return a run_id")
        accepted_statuses = {
            "created",
            "enqueuing",
            "queued",
            "running",
            "processed_inline",
        }
        if result.get("duplicate"):
            accepted_statuses.update(_TERMINAL_RUN_STATUSES)
        if result.get("status") not in accepted_statuses:
            raise RuntimeError(f"Unexpected price refresh status: {result.get('status')}")
    except Exception as exc:
        run_id = _record_control_failure(scheduled_at, exc) if scheduled_at else None
        payload = {
            "status": "failed",
            "error": type(exc).__name__,
            "message": str(exc),
            "run_id": run_id,
            "traceback": traceback.format_exc(limit=24),
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        _write_termination_message(payload)
        raise
    else:
        payload = {
            "status": "success",
            "scheduled_at": scheduled_at.isoformat(),
            "result": result,
        }
        print("PRICE_REFRESH_START=SUCCESS", flush=True)
        print(
            "PRICE_REFRESH_RESULT=" + json.dumps(payload, ensure_ascii=False, sort_keys=True),
            flush=True,
        )
        _write_termination_message(payload)


if __name__ == "__main__":
    main()
