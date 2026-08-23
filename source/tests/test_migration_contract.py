from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.apply_migrations import discover_migrations


def test_migrations_are_sorted_and_checksum_pinned(tmp_path: Path) -> None:
    (tmp_path / "002_second.sql").write_text("SELECT 2;\n", encoding="utf-8")
    (tmp_path / "001_first.sql").write_text("SELECT 1;\n", encoding="utf-8")

    migrations = discover_migrations(tmp_path)

    assert [migration.version for migration in migrations] == [
        "001_first.sql",
        "002_second.sql",
    ]
    assert migrations[0].checksum == hashlib.sha256(b"SELECT 1;\n").hexdigest()


def test_repository_migrations_have_unique_monotonic_names() -> None:
    migrations = discover_migrations()
    names = [migration.version for migration in migrations]

    assert len(names) == len(set(names))
    assert names == sorted(names)
    assert names[0].startswith("001_")


def test_migration_checksums_are_independent_of_line_endings(tmp_path: Path) -> None:
    lf_directory = tmp_path / "lf"
    crlf_directory = tmp_path / "crlf"
    lf_directory.mkdir()
    crlf_directory.mkdir()
    (lf_directory / "001_example.sql").write_bytes(b"SELECT 1;\nSELECT 2;\n")
    (crlf_directory / "001_example.sql").write_bytes(b"SELECT 1;\r\nSELECT 2;\r\n")

    assert discover_migrations(lf_directory)[0].checksum == discover_migrations(crlf_directory)[0].checksum
