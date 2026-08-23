from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import UTC

from app.schemas import CatalogDiscoveryTaskPayload, ScrapeGroupPayload
from app.settings import get_settings

logger = logging.getLogger(__name__)


class TaskEnqueuer:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._client = None

    def _cloud_client(self):
        if self._client is None:
            from google.cloud import tasks_v2

            self._client = tasks_v2.CloudTasksClient()
        return self._client

    def enqueue(self, payload: ScrapeGroupPayload) -> str:
        if self.settings.tasks_mode == "inline":
            from app.repository_provider import repository
            from app.scraping.engine import ScrapeEngine

            engine = ScrapeEngine()
            for attempt in range(1, self.settings.cloud_tasks_max_attempts + 1):
                result = engine.process(payload)
                if result.get("status") != "retryable_failed":
                    return payload.task_id
                if attempt >= self.settings.cloud_tasks_max_attempts:
                    repository.promote_retry_exhausted(payload.task_id)
                    return payload.task_id
                retry_after = int(result.get("retry_after_seconds") or 0)
                time.sleep(min(max(retry_after, 2 ** (attempt - 1)), 30))
            return payload.task_id

        return self._enqueue_cloud(
            payload,
            queue_name=self.settings.cloud_tasks_queue,
            target_url=self.settings.worker_task_url,
        )

    def enqueue_catalog(self, payload: CatalogDiscoveryTaskPayload) -> str:
        if self.settings.tasks_mode == "inline":
            from app.scraping.catalog_discovery import CatalogDiscoveryEngine

            engine = CatalogDiscoveryEngine()
            for attempt in range(1, self.settings.cloud_tasks_max_attempts + 1):
                result = engine.process(payload)
                if result.get("status") != "retryable_failed":
                    return (
                        f"{payload.task_id}:delivery:{payload.delivery_generation}"
                    )
                if attempt >= self.settings.cloud_tasks_max_attempts:
                    return (
                        f"{payload.task_id}:delivery:{payload.delivery_generation}"
                    )
                retry_after = int(result.get("retry_after_seconds") or 0)
                time.sleep(min(max(retry_after, 2 ** (attempt - 1)), 30))
            return f"{payload.task_id}:delivery:{payload.delivery_generation}"

        return self._enqueue_cloud(
            payload,
            queue_name=self.settings.catalog_tasks_queue,
            target_url=self.settings.catalog_worker_task_url,
        )

    def _enqueue_cloud(self, payload, *, queue_name: str, target_url: str) -> str:
        if not self.settings.gcp_project_id:
            raise RuntimeError("GCP_PROJECT_ID is required when TASKS_MODE=cloud")
        if not self.settings.tasks_service_account_email:
            raise RuntimeError("TASKS_SERVICE_ACCOUNT_EMAIL is required when TASKS_MODE=cloud")

        from google.cloud import tasks_v2
        from google.protobuf import duration_pb2, timestamp_pb2

        client = self._cloud_client()
        parent = client.queue_path(
            self.settings.gcp_project_id,
            self.settings.cloud_tasks_location,
            queue_name,
        )
        body = json.dumps(payload.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        generation = int(getattr(payload, "delivery_generation", 1))
        task_hash = hashlib.sha1(
            f"{payload.task_id}|delivery:{generation}".encode()
        ).hexdigest()
        task_name = f"{parent}/tasks/sa3arly-{task_hash}"

        schedule_time = timestamp_pb2.Timestamp()
        schedule_time.FromDatetime(payload.scheduled_for.astimezone(UTC))

        task = tasks_v2.Task(
            name=task_name,
            schedule_time=schedule_time,
            dispatch_deadline=duration_pb2.Duration(
                seconds=self.settings.task_dispatch_deadline_seconds
            ),
            http_request=tasks_v2.HttpRequest(
                http_method=tasks_v2.HttpMethod.POST,
                url=target_url,
                headers={"Content-Type": "application/json"},
                body=body,
                oidc_token=tasks_v2.OidcToken(
                    service_account_email=self.settings.tasks_service_account_email,
                    audience=self.settings.worker_url.rstrip("/"),
                ),
            ),
        )
        for enqueue_attempt in range(1, 4):
            try:
                response = client.create_task(parent=parent, task=task)
                return response.name
            except Exception as exc:
                # Deterministic names make a retry safe when the first request may
                # have reached Cloud Tasks but its response was lost.
                if exc.__class__.__name__ == "AlreadyExists":
                    logger.info("Task already exists", extra={"task_id": payload.task_id})
                    return task_name
                transient = exc.__class__.__name__ in {
                    "Aborted",
                    "DeadlineExceeded",
                    "InternalServerError",
                    "ResourceExhausted",
                    "ServiceUnavailable",
                    "TooManyRequests",
                }
                if not transient or enqueue_attempt >= 3:
                    raise
                time.sleep(min(2 ** (enqueue_attempt - 1), 4))
        raise RuntimeError("Cloud Tasks enqueue loop ended unexpectedly")
