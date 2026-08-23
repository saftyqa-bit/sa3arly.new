from __future__ import annotations

from contextlib import contextmanager

from app import repository


class Result:
    def __init__(self, *, one=None, many=None):
        self.one = one
        self.many = many or []

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class Connection:
    def __init__(self):
        self.calls = []

    def execute(self, query, params):
        compact_query = " ".join(query.split())
        self.calls.append((compact_query, params))
        if compact_query.startswith("SELECT * FROM price_runs"):
            return Result(one={"run_id": "RUN-1", "status": "running"})
        if compact_query.startswith("SELECT COUNT(*) AS count"):
            return Result(one={"count": 1149})
        return Result(
            many=[
                {
                    "external_task_id": "TASK-501",
                    "store_id": "EG-013",
                    "source_url": "https://btech.com/en/p/example",
                    "status": "queued",
                }
            ]
        )


def test_postgres_run_status_uses_limit_offset_and_stable_order(monkeypatch):
    fake = Connection()

    @contextmanager
    def fake_connection():
        yield fake

    monkeypatch.setattr(repository, "connection", fake_connection)

    result = repository.get_run("RUN-1", task_limit=250, task_offset=500)

    assert result is not None
    assert result["pagination"] == {
        "limit": 250,
        "offset": 500,
        "returned_task_rows": 1,
        "total_task_rows": 1149,
        "has_more": True,
    }
    tasks_query, tasks_params = fake.calls[2]
    assert "ORDER BY scheduled_for, store_id, external_task_id" in tasks_query
    assert "LIMIT %s OFFSET %s" in tasks_query
    assert tasks_params == ("RUN-1", 250, 500)
