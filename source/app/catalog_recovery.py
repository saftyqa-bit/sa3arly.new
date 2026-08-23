from __future__ import annotations

import logging
from typing import Any

from app.repository_provider import repository
from app.tasks_client import TaskEnqueuer

logger = logging.getLogger(__name__)


def recover_catalog_discovery_tasks(*, limit: int = 50) -> dict[str, Any]:
    """Re-enqueue logical catalog work whose physical delivery went silent."""

    prepare = getattr(repository, "prepare_catalog_task_recoveries", None)
    mark_enqueued = getattr(repository, "mark_catalog_task_enqueued", None)
    mark_failed = getattr(
        repository,
        "mark_catalog_task_recovery_enqueue_failed",
        None,
    )
    if not all(callable(method) for method in (prepare, mark_enqueued, mark_failed)):
        return {
            "status": "unsupported_backend",
            "prepared": 0,
            "enqueued": 0,
            "failed": 0,
        }

    payloads = prepare(limit=limit)
    enqueuer = TaskEnqueuer()
    enqueued = 0
    failures: list[dict[str, str | int]] = []
    for payload in payloads:
        try:
            queue_task_name = enqueuer.enqueue_catalog(payload)
            mark_enqueued(
                payload.task_id,
                delivery_generation=payload.delivery_generation,
                queue_task_name=queue_task_name,
            )
            enqueued += 1
        except Exception as exc:
            logger.exception(
                "Catalog task recovery enqueue failed",
                extra={
                    "task_id": payload.task_id,
                    "delivery_generation": payload.delivery_generation,
                },
            )
            mark_failed(
                payload.task_id,
                delivery_generation=payload.delivery_generation,
                error_message=str(exc),
            )
            failures.append(
                {
                    "task_id": payload.task_id,
                    "delivery_generation": payload.delivery_generation,
                    "error": str(exc)[:500],
                }
            )
    return {
        "status": "recovered" if payloads else "idle",
        "prepared": len(payloads),
        "enqueued": enqueued,
        "failed": len(failures),
        "failures": failures,
    }
