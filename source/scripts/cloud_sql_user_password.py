#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_ROOT = "https://sqladmin.googleapis.com/sql/v1beta4"


def access_token() -> str:
    result = subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        check=True,
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("gcloud returned an empty access token")
    return token


def request_json(
    token: str,
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Cloud SQL Admin API returned HTTP {exc.code}: {detail}") from exc
    return json.loads(raw or b"{}")


def wait_operation(token: str, project: str, operation: str) -> None:
    url = f"{API_ROOT}/projects/{urllib.parse.quote(project, safe='')}/operations/{urllib.parse.quote(operation, safe='')}"
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        result = request_json(token, "GET", url)
        if result.get("status") == "DONE":
            if result.get("error"):
                raise RuntimeError(f"Cloud SQL operation failed: {json.dumps(result['error'])}")
            return
        time.sleep(2)
    raise TimeoutError("Timed out waiting for the Cloud SQL user operation")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or update a Cloud SQL user without placing its password in argv."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--instance", required=True)
    parser.add_argument("--user", required=True)
    args = parser.parse_args()

    password = sys.stdin.read()
    if password.endswith("\n"):
        password = password[:-1]
    if not password:
        raise ValueError("Database password was not provided on stdin")

    token = access_token()
    project = urllib.parse.quote(args.project, safe="")
    instance = urllib.parse.quote(args.instance, safe="")
    users_url = f"{API_ROOT}/projects/{project}/instances/{instance}/users"
    users = request_json(token, "GET", users_url).get("items") or []
    existing = next((item for item in users if item.get("name") == args.user), None)

    if existing:
        query = urllib.parse.urlencode(
            {"name": args.user, "host": existing.get("host") or "%"}
        )
        operation = request_json(
            token,
            "PUT",
            f"{users_url}?{query}",
            {"password": password},
        )
    else:
        operation = request_json(
            token,
            "POST",
            users_url,
            {"name": args.user, "password": password},
        )

    operation_name = str(operation.get("name") or "")
    if not operation_name:
        raise RuntimeError("Cloud SQL Admin API did not return an operation name")
    wait_operation(token, args.project, operation_name)
    print("CLOUD_SQL_USER_PASSWORD=CONFIGURED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
