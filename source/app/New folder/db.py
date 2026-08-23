from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.settings import get_settings

_pool: ConnectionPool | None = None

DATABASE_SEARCH_PATH = (
    "reference,catalog,merchant,pricing,ingestion,operations,"
    "governance,analytics,public"
)


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        settings = get_settings()
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            kwargs={
                "row_factory": dict_row,
                "options": f"-c search_path={DATABASE_SEARCH_PATH}",
            },
            open=True,
        )
    return _pool


@contextmanager
def connection() -> Iterator[Connection]:
    with get_pool().connection() as conn:
        yield conn


@contextmanager
def transaction() -> Iterator[Connection]:
    with get_pool().connection() as conn:
        with conn.transaction():
            yield conn


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def healthcheck() -> bool:
    with connection() as conn:
        row = conn.execute("SELECT 1 AS ok").fetchone()
        return bool(row and row["ok"] == 1)
