from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime

from app.schedule import next_refresh_at

WORKER_URL = os.environ.get("WORKER_URL", "http://worker:8080").rstrip("/")
TOKEN = os.environ.get("INTERNAL_TOKEN", "change-this-local-token")
TRIGGER_ATTEMPTS = max(1, int(os.environ.get("LOCAL_SCHEDULER_TRIGGER_ATTEMPTS", "3")))


def seconds_to_next_refresh() -> float:
    now = datetime.now().astimezone()
    return max((next_refresh_at(now) - now).total_seconds(), 1)


def trigger_once() -> str:
    request = urllib.request.Request(
        WORKER_URL + "/internal/scheduler/refresh",
        data=json.dumps({"trigger": "local-hourly-scheduler"}).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Internal-Token": TOKEN,
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8")


def trigger_with_retry() -> None:
    last_error: Exception | None = None
    for attempt in range(1, TRIGGER_ATTEMPTS + 1):
        try:
            print(trigger_once(), flush=True)
            return
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt >= TRIGGER_ATTEMPTS:
                break
            delay = min(2 ** (attempt - 1) * 10, 60)
            print(
                f"Scheduler trigger attempt {attempt}/{TRIGGER_ATTEMPTS} failed: {exc}; "
                f"retrying in {delay}s",
                flush=True,
            )
            time.sleep(delay)
    raise RuntimeError(f"Local scheduler trigger failed after {TRIGGER_ATTEMPTS} attempts: {last_error}")


if __name__ == "__main__":
    print("Local hourly scheduler started", flush=True)
    while True:
        time.sleep(seconds_to_next_refresh())
        try:
            trigger_with_retry()
        except Exception as exc:
            # Continue to the next refresh; the run itself is idempotent, so a
            # manual retry can also be made without duplicating the cadence slot.
            print(f"Scheduler trigger failed: {exc}", flush=True)
