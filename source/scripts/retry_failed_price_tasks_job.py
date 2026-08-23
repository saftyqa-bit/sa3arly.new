from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

from app.hourly import create_refresh_run


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m scripts.retry_failed_price_tasks_job SOURCE_RUN_ID")
    source_run_id = sys.argv[1].strip()
    if not source_run_id:
        raise SystemExit("SOURCE_RUN_ID is required")

    result = create_refresh_run(
        "failed-only-retry",
        scheduled_at=datetime.now(UTC),
        retry_source_run_id=source_run_id,
    )
    if not result.get("run_id"):
        raise RuntimeError("Failed-task retry did not return a run_id")
    payload = {
        "status": "success",
        "source_run_id": source_run_id,
        "result": result,
    }
    print("PRICE_FAILED_RETRY=" + json.dumps(payload, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
