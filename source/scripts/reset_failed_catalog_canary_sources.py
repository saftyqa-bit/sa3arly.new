from __future__ import annotations

import json
import os

from app.db import transaction

EXPECTED_ERROR = "could not determine data type of parameter $2"


def main() -> None:
    run_id = os.environ.get("FAILED_CATALOG_RUN_ID", "").strip()
    if not run_id:
        raise SystemExit("FAILED_CATALOG_RUN_ID is required")

    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT source_id, store_id, error_code, error_message
            FROM catalog_discovery_tasks
            WHERE run_id = %s
            ORDER BY store_id, source_id
            """,
            (run_id,),
        ).fetchall()
        if len(rows) != 5:
            raise RuntimeError(
                f"Expected exactly five failed canary tasks for {run_id}; found {len(rows)}"
            )

        unexpected = [
            dict(row)
            for row in rows
            if row.get("error_code") != "internal_error"
            or EXPECTED_ERROR not in str(row.get("error_message") or "")
        ]
        if unexpected:
            raise RuntimeError(
                "Refusing to reset sources because the failed run does not match "
                f"the v0.5.0 PostgreSQL parameter-type defect: {unexpected}"
            )

        source_ids = [str(row["source_id"]) for row in rows]
        reset = conn.execute(
            """
            UPDATE catalog_discovery_sources
            SET next_scan_at = NOW(), updated_at = NOW()
            WHERE source_id = ANY(%s::text[])
            RETURNING source_id
            """,
            (source_ids,),
        ).fetchall()
        if len(reset) != len(source_ids):
            raise RuntimeError(
                f"Expected to reset {len(source_ids)} sources; reset {len(reset)}"
            )

    print(
        json.dumps(
            {
                "failed_run_id": run_id,
                "reset_source_count": len(source_ids),
                "store_ids": [str(row["store_id"]) for row in rows],
                "status": "ready_for_v0.5.1_canary",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
