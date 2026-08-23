from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from app.schemas import CatalogBootstrapImportRequest


def read_records(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.casefold()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".jsonl":
        with path.open("r", encoding="utf-8-sig") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, list):
            return [dict(item) for item in payload]
        if isinstance(payload, dict):
            for key in ("records", "rows", "results", "data"):
                if isinstance(payload.get(key), list):
                    return [dict(item) for item in payload[key]]
        raise ValueError("JSON input must be a list or contain records/rows/results/data")
    raise ValueError("Input must be .csv, .json, or .jsonl")


def post_to_api(api_url: str, token: str, payload: dict[str, Any]) -> dict[str, Any]:
    endpoint = api_url.rstrip("/") + "/internal/catalog/bootstrap/import"
    request = Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Internal-Token": token,
        },
        method="POST",
    )
    with urlopen(request, timeout=300) as response:  # noqa: S310 - explicit operator URL
        return json.loads(response.read().decode("utf-8"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely stage and match an Ultimate Web Scraper product export."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--store-id", required=True)
    parser.add_argument("--external-run-id", required=True)
    parser.add_argument("--provider", default="ultimate_web_scraper")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--api-url",
        help="Call a deployed Sa3arly API. Without this, DATABASE_URL is used directly.",
    )
    parser.add_argument("--token-env", default="INTERNAL_TOKEN")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = read_records(args.input)
    request = CatalogBootstrapImportRequest(
        provider=args.provider,
        external_run_id=args.external_run_id,
        store_id=args.store_id,
        records=records,
        dry_run=args.dry_run,
        metadata={"input_file": args.input.name},
    )
    if args.api_url:
        token = os.environ.get(args.token_env, "")
        if not token:
            raise SystemExit(f"{args.token_env} is required with --api-url")
        result = post_to_api(
            args.api_url,
            token,
            request.model_dump(mode="json"),
        )
    else:
        if not os.environ.get("DATABASE_URL"):
            raise SystemExit("DATABASE_URL is required without --api-url")
        from app.repository import ingest_catalog_bootstrap

        result = ingest_catalog_bootstrap(request)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
