from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "db" / "migrations"
LOCK_NAME = "sa3arly_schema_migrations_v2"
LEDGER = "governance.schema_migrations"


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    checksum: str


def discover_migrations(directory: Path = MIGRATIONS) -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        # Migration checksums must not depend on the developer or runner OS.
        # Text files written or checked out on Windows may contain CRLF even
        # though the same migration uses LF in Linux production.
        content = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        migrations.append(
            Migration(
                version=path.name,
                path=path,
                checksum=hashlib.sha256(content).hexdigest(),
            )
        )
    if not migrations:
        raise RuntimeError(f"No SQL migrations found in {directory}")
    return migrations


def ensure_ledger(conn: Any) -> None:
    conn.execute(
        """
        CREATE SCHEMA IF NOT EXISTS governance;
        CREATE TABLE IF NOT EXISTS governance.schema_migrations (
            version TEXT PRIMARY KEY,
            checksum_sha256 CHAR(64) NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    conn.commit()


def applied_migrations(conn: Any) -> dict[str, str]:
    rows = conn.execute(
        f"SELECT version, checksum_sha256 FROM {LEDGER} ORDER BY version"
    ).fetchall()
    return {str(version): str(checksum) for version, checksum in rows}


def apply_all(database_url: str, directory: Path = MIGRATIONS) -> list[str]:
    import psycopg

    migrations = discover_migrations(directory)
    applied_now: list[str] = []

    with psycopg.connect(database_url) as conn:
        # Prevent two deployments from changing the schema concurrently.
        conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (LOCK_NAME,))
        try:
            ensure_ledger(conn)
            existing = applied_migrations(conn)

            for migration in migrations:
                known_checksum = existing.get(migration.version)
                if known_checksum:
                    if known_checksum != migration.checksum:
                        raise RuntimeError(
                            "Applied migration checksum changed: "
                            f"{migration.version} expected={known_checksum} "
                            f"actual={migration.checksum}"
                        )
                    print(f"Skipping {migration.version}; already applied")
                    continue

                print(f"Applying {migration.version}")
                try:
                    conn.execute(migration.path.read_text(encoding="utf-8"))
                    conn.execute(
                        """
                        INSERT INTO governance.schema_migrations (
                            version, checksum_sha256
                        )
                        VALUES (%s, %s)
                        """,
                        (migration.version, migration.checksum),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
                applied_now.append(migration.version)
        finally:
            conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))
            conn.commit()

    return applied_now


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required")

    applied_now = apply_all(database_url)
    print("SCHEMA_MIGRATIONS=PASS")
    print(f"APPLIED_COUNT={len(applied_now)}")
    for version in applied_now:
        print(f"APPLIED={version}")


if __name__ == "__main__":
    main()
