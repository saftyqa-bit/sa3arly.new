from __future__ import annotations

from functools import lru_cache
from types import ModuleType
from typing import Any

from app.settings import get_settings


@lru_cache(maxsize=1)
def get_repository() -> Any:
    """Return the configured persistence implementation.

    PostgreSQL is the production default. Firestore remains an explicit legacy
    choice for rollback and migration verification only.
    """

    if get_settings().persistence_backend == "firestore":
        from app.firestore_repository import FirestoreRepository

        return FirestoreRepository()

    from app import repository as postgres_repository

    return postgres_repository


repository: ModuleType | Any = get_repository()


def repository_healthcheck() -> bool:
    if get_settings().persistence_backend == "firestore":
        return bool(repository.healthcheck())

    from app.db import healthcheck

    return healthcheck()


def close_repository() -> None:
    if get_settings().persistence_backend == "firestore":
        close = getattr(repository, "close", None)
        if close:
            close()
        return

    from app.db import close_pool

    close_pool()
