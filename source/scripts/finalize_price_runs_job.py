from __future__ import annotations

import json

from app.repository_provider import repository
from app.settings import get_settings


def main() -> None:
    settings = get_settings()
    result = repository.finalize_overdue_price_runs(settings.price_run_finalization_deadline_minutes)
    payload = {
        "status": "finalized" if result["runs_finalized"] else "idle",
        **result,
    }
    print("PRICE_RUN_FINALIZER=" + json.dumps(payload, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
