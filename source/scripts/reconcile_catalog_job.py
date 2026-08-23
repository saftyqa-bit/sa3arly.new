from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from scripts.reconcile_catalog_candidates import reconcile_all

_TERMINATION_LOG = Path("/dev/termination-log")
_TERMINATION_LIMIT = 4096


def _write_termination_message(payload: dict) -> None:
    message = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    _TERMINATION_LOG.write_text(message[-_TERMINATION_LIMIT:], encoding="utf-8")


def main() -> None:
    try:
        totals = reconcile_all()
    except Exception as exc:
        trace = traceback.format_exc(limit=24)
        payload = {
            "error": type(exc).__name__,
            "message": str(exc),
            "traceback": trace,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        _write_termination_message(payload)
        raise
    else:
        payload = {"status": "success", "totals": totals}
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)
        _write_termination_message(payload)


if __name__ == "__main__":
    main()
