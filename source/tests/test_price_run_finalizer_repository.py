from __future__ import annotations

from contextlib import nullcontext

from app import repository


class Result:
    def __init__(self, *, rows=None, row=None):
        self.rows = rows or []
        self.row = row

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.row


class Connection:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, query, params):
        sql = str(query)
        self.calls.append((sql, params))
        if "SELECT run_id" in sql and "FROM price_runs" in sql:
            return Result(rows=[{"run_id": "00000000-0000-0000-0000-000000000001"}])
        if "UPDATE price_tasks" in sql:
            return Result(rows=[{"run_id": "run"}] * 94)
        if "COUNT(*) FILTER" in sql:
            return Result(row={"completed": 16116, "succeeded": 1019, "failed": 15097})
        return Result()


def test_finalizer_terminalizes_tasks_and_rebuilds_run_counters(monkeypatch):
    conn = Connection()
    monkeypatch.setattr(repository, "transaction", lambda: nullcontext(conn))

    result = repository.finalize_overdue_price_runs(360)

    assert result == {
        "deadline_minutes": 360,
        "runs_finalized": 1,
        "tasks_finalized": 94,
        "run_ids": ["00000000-0000-0000-0000-000000000001"],
    }
    task_sql, task_params = conn.calls[1]
    assert "status NOT IN ('success', 'failed')" in task_sql
    assert "previous_status" in task_sql
    assert task_params == (["00000000-0000-0000-0000-000000000001"],)

    run_sql, run_params = conn.calls[3]
    assert "completed_with_errors" in run_sql
    assert "deadline_finalized" in run_sql
    assert run_params[:5] == (16116, 1019, 15097, 15097, 360)


def test_late_worker_result_cannot_overwrite_a_finalized_task(monkeypatch):
    class TerminalConnection:
        def __init__(self):
            self.calls = 0

        def execute(self, _query, _params):
            self.calls += 1
            return Result(
                row={
                    "run_id": "00000000-0000-0000-0000-000000000001",
                    "status": "failed",
                }
            )

    conn = TerminalConnection()
    monkeypatch.setattr(repository, "transaction", lambda: nullcontext(conn))

    repository.finish_task("TASK-1", status="success", cash_updates=1)

    assert conn.calls == 1


def test_repair_terminal_run_rebuilds_parent_state(monkeypatch):
    class RepairConnection:
        def __init__(self):
            self.calls = []

        def execute(self, query, params):
            sql = str(query)
            self.calls.append((sql, params))
            if "SELECT status FROM price_runs" in sql:
                return Result(row={"status": "enqueue_failed"})
            if "COUNT(*) AS total" in sql:
                return Result(
                    row={
                        "total": 100,
                        "completed": 100,
                        "succeeded": 25,
                        "failed": 75,
                    }
                )
            return Result()

    conn = RepairConnection()
    monkeypatch.setattr(repository, "transaction", lambda: nullcontext(conn))

    result = repository.repair_terminal_price_run("00000000-0000-0000-0000-000000000001")

    assert result["status"] == "completed_with_errors"
    assert result["repaired"] is True
    update_sql = conn.calls[-1][0]
    assert "terminal_state_repaired" in update_sql


def test_enqueue_failure_cannot_overwrite_a_completed_run(monkeypatch):
    conn = Connection()
    monkeypatch.setattr(repository, "transaction", lambda: nullcontext(conn))

    repository.mark_run_enqueue_failed(
        "00000000-0000-0000-0000-000000000001",
        "late control failure",
    )

    sql = conn.calls[-1][0]
    assert "status IN ('created', 'enqueuing', 'queued', 'running')" in sql
    assert "completed_at IS NULL" in sql
