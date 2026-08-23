from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scripts.reconcile_catalog_candidates import reconcile_all

_reconcile_lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/healthz":
            self._send(200, {"status": "ok"})
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/reconcile":
            self._send(404, {"error": "not_found"})
            return
        expected_token = os.environ.get("RECONCILE_SHARED_TOKEN", "")
        provided_token = self.headers.get("X-Sa3arly-Reconcile-Token", "")
        if not expected_token or not secrets.compare_digest(expected_token, provided_token):
            self._send(401, {"error": "unauthorized"})
            return
        if not _reconcile_lock.acquire(blocking=False):
            self._send(409, {"error": "reconciliation_already_running"})
            return
        try:
            totals = reconcile_all()
        except Exception as exc:  # Return the private workflow an actionable failure.
            trace = traceback.format_exc(limit=24)
            print(trace, file=sys.stderr, flush=True)
            self._send(
                500,
                {
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "traceback": trace,
                },
            )
        else:
            self._send(200, {"status": "success", "totals": totals})
        finally:
            _reconcile_lock.release()

    def log_message(self, format: str, *args) -> None:
        print(format % args, file=sys.stderr, flush=True)


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
