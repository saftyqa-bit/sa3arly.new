from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from app import catalog_recovery
from app.schemas import CatalogDiscoveryTaskPayload
from app.tasks_client import TaskEnqueuer


def _payload(generation: int) -> CatalogDiscoveryTaskPayload:
    now = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)
    return CatalogDiscoveryTaskPayload(
        task_id="CAT-RECOVERY-1",
        run_id="00000000-0000-0000-0000-000000000010",
        run_slot=now,
        scheduled_for=now,
        source_id="CDS-EG-TEST",
        store_id="EG-TEST",
        store_name="Example",
        source_url="https://shop.example/",
        delivery_generation=generation,
    )


def test_cloud_task_name_changes_for_each_delivery_generation(monkeypatch) -> None:
    enqueuer = TaskEnqueuer()
    names: list[str] = []

    class FakeClient:
        @staticmethod
        def queue_path(project, location, queue):
            return f"projects/{project}/locations/{location}/queues/{queue}"

        @staticmethod
        def create_task(*, parent, task):
            names.append(task.name)
            return SimpleNamespace(name=task.name)

    class FakeTimestamp:
        def FromDatetime(self, value):
            self.value = value

    class FakeTask:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    fake_tasks = SimpleNamespace(
        Task=FakeTask,
        HttpRequest=lambda **kwargs: kwargs,
        HttpMethod=SimpleNamespace(POST="POST"),
        OidcToken=lambda **kwargs: kwargs,
    )
    fake_duration = SimpleNamespace(Duration=lambda **kwargs: kwargs)
    fake_timestamp = SimpleNamespace(Timestamp=FakeTimestamp)
    monkeypatch.setattr(enqueuer, "_cloud_client", lambda: FakeClient())
    monkeypatch.setattr(
        enqueuer,
        "settings",
        SimpleNamespace(
            gcp_project_id="project",
            cloud_tasks_location="region",
            tasks_service_account_email="tasks@example.test",
            worker_url="https://worker.example.test",
            task_dispatch_deadline_seconds=900,
        ),
    )
    monkeypatch.setitem(__import__("sys").modules, "google.cloud.tasks_v2", fake_tasks)
    monkeypatch.setitem(__import__("sys").modules, "google.protobuf.duration_pb2", fake_duration)
    monkeypatch.setitem(__import__("sys").modules, "google.protobuf.timestamp_pb2", fake_timestamp)

    first = enqueuer._enqueue_cloud(
        _payload(1), queue_name="catalog", target_url="https://worker/task"
    )
    second = enqueuer._enqueue_cloud(
        _payload(2), queue_name="catalog", target_url="https://worker/task"
    )

    assert first != second
    assert names == [first, second]


def test_recovery_marks_every_new_delivery(monkeypatch) -> None:
    payloads = [_payload(2), _payload(3)]
    marked: list[tuple[str, int, str]] = []
    fake_repository = SimpleNamespace(
        prepare_catalog_task_recoveries=lambda *, limit: payloads,
        mark_catalog_task_enqueued=lambda task_id, delivery_generation, queue_task_name: marked.append(
            (task_id, delivery_generation, queue_task_name)
        ),
        mark_catalog_task_recovery_enqueue_failed=lambda *args, **kwargs: None,
    )

    class FakeEnqueuer:
        @staticmethod
        def enqueue_catalog(payload):
            return f"queue/{payload.delivery_generation}"

    monkeypatch.setattr(catalog_recovery, "repository", fake_repository)
    monkeypatch.setattr(catalog_recovery, "TaskEnqueuer", FakeEnqueuer)

    result = catalog_recovery.recover_catalog_discovery_tasks(limit=2)

    assert result["status"] == "recovered"
    assert result["enqueued"] == 2
    assert marked == [
        ("CAT-RECOVERY-1", 2, "queue/2"),
        ("CAT-RECOVERY-1", 3, "queue/3"),
    ]
