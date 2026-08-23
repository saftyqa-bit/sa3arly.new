from __future__ import annotations

import hashlib
import json
import os
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import psycopg
from psycopg import sql

EXPORT_PATH = Path("/tmp/sa3arly-production-data.zip")
MANIFEST_PATH = Path("/tmp/sa3arly-production-data.manifest.json")
APP_SCHEMAS = (
    "public",
    "reference",
    "catalog",
    "merchant",
    "pricing",
    "ingestion",
    "operations",
    "governance",
    "analytics",
)
MAX_CHUNK_BYTES = 8 * 1024 * 1024
_build_lock = threading.Lock()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_rows(cursor: psycopg.Cursor) -> list[dict[str, object]]:
    columns = [description.name for description in cursor.description or ()]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _metadata(conn: psycopg.Connection) -> dict[str, object]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT table_schema, table_name, ordinal_position, column_name,
                   data_type, udt_schema, udt_name, is_nullable, column_default,
                   is_identity, identity_generation, is_generated,
                   generation_expression
              FROM information_schema.columns
             WHERE table_schema = ANY(%s)
             ORDER BY table_schema, table_name, ordinal_position
            """,
            (list(APP_SCHEMAS),),
        )
        columns = _json_rows(cursor)
        cursor.execute(
            """
            SELECT namespace.nspname AS table_schema,
                   relation.relname AS table_name,
                   constraint_record.conname AS constraint_name,
                   constraint_record.contype AS constraint_type,
                   pg_get_constraintdef(constraint_record.oid, true) AS definition
              FROM pg_constraint AS constraint_record
              JOIN pg_class AS relation
                ON relation.oid = constraint_record.conrelid
              JOIN pg_namespace AS namespace
                ON namespace.oid = relation.relnamespace
             WHERE namespace.nspname = ANY(%s)
             ORDER BY namespace.nspname, relation.relname,
                      constraint_record.conname
            """,
            (list(APP_SCHEMAS),),
        )
        constraints = _json_rows(cursor)
        cursor.execute(
            """
            SELECT schemaname AS table_schema, tablename AS table_name,
                   indexname AS index_name, indexdef AS definition
              FROM pg_indexes
             WHERE schemaname = ANY(%s)
             ORDER BY schemaname, tablename, indexname
            """,
            (list(APP_SCHEMAS),),
        )
        indexes = _json_rows(cursor)
        cursor.execute(
            """
            SELECT schemaname AS view_schema, viewname AS view_name,
                   definition
              FROM pg_views
             WHERE schemaname = ANY(%s)
             ORDER BY schemaname, viewname
            """,
            (list(APP_SCHEMAS),),
        )
        views = _json_rows(cursor)
        cursor.execute(
            """
            SELECT schemaname AS sequence_schema, sequencename AS sequence_name,
                   data_type, start_value, min_value, max_value, increment_by,
                   cycle, cache_size, last_value
              FROM pg_sequences
             WHERE schemaname = ANY(%s)
             ORDER BY schemaname, sequencename
            """,
            (list(APP_SCHEMAS),),
        )
        sequences = _json_rows(cursor)
    return {
        "schemas": list(APP_SCHEMAS),
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
        "views": views,
        "sequences": sequences,
    }


def _build_export() -> dict[str, object]:
    with _build_lock:
        if EXPORT_PATH.is_file() and MANIFEST_PATH.is_file():
            return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

        database_url = os.environ["DATABASE_URL"]
        temporary_path = EXPORT_PATH.with_suffix(".zip.partial")
        temporary_path.unlink(missing_ok=True)
        tables: list[dict[str, object]] = []

        with psycopg.connect(database_url) as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            schema_metadata = _metadata(conn)
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT table_schema, table_name
                      FROM information_schema.tables
                     WHERE table_type = 'BASE TABLE'
                       AND table_schema = ANY(%s)
                     ORDER BY table_schema, table_name
                    """,
                    (list(APP_SCHEMAS),),
                )
                table_names = cursor.fetchall()

            with zipfile.ZipFile(
                temporary_path,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                allowZip64=True,
            ) as archive:
                for schema_name, table_name in table_names:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT column_name
                              FROM information_schema.columns
                             WHERE table_schema = %s AND table_name = %s
                               AND is_generated = 'NEVER'
                             ORDER BY ordinal_position
                            """,
                            (schema_name, table_name),
                        )
                        column_names = [row[0] for row in cursor.fetchall()]
                        cursor.execute(
                            sql.SQL("SELECT count(*) FROM {}.{}").format(
                                sql.Identifier(schema_name),
                                sql.Identifier(table_name),
                            )
                        )
                        row_count = int(cursor.fetchone()[0])

                    archive_name = f"tables/{schema_name}/{table_name}.csv"
                    copy_statement = sql.SQL(
                        "COPY {}.{} ({}) TO STDOUT WITH (FORMAT CSV, HEADER TRUE)"
                    ).format(
                        sql.Identifier(schema_name),
                        sql.Identifier(table_name),
                        sql.SQL(", ").join(map(sql.Identifier, column_names)),
                    )
                    with archive.open(archive_name, "w", force_zip64=True) as target:
                        with conn.cursor() as cursor:
                            with cursor.copy(copy_statement) as copy:
                                for chunk in copy:
                                    target.write(bytes(chunk))
                    tables.append(
                        {
                            "schema": schema_name,
                            "table": table_name,
                            "columns": column_names,
                            "rows": row_count,
                            "archive_path": archive_name,
                        }
                    )

                archive.writestr(
                    "metadata/schema.json",
                    json.dumps(
                        schema_metadata,
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    )
                    + "\n",
                )
                archive.writestr(
                    "README-RESTORE.txt",
                    "Apply the SQL migrations from the matching source archive, then "
                    "load every CSV listed in metadata/manifest.json with PostgreSQL "
                    "COPY. The Cloud SQL backup manifest in the outer handoff is the "
                    "preferred exact-instance restore path. No credentials are included.\n",
                )

        content_manifest: dict[str, object] = {
            "format": "sa3arly-logical-csv-v1",
            "consistent_snapshot": True,
            "tables": tables,
            "table_count": len(tables),
            "total_rows": sum(int(table["rows"]) for table in tables),
        }
        with zipfile.ZipFile(temporary_path, mode="a") as archive:
            archive.writestr(
                "metadata/manifest.json",
                json.dumps(content_manifest, ensure_ascii=False, indent=2) + "\n",
            )
        temporary_path.replace(EXPORT_PATH)
        manifest = dict(content_manifest)
        manifest["size_bytes"] = EXPORT_PATH.stat().st_size
        manifest["sha256"] = _sha256(EXPORT_PATH)
        MANIFEST_PATH.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return manifest


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def _authorized(self) -> bool:
        return self.headers.get("X-Export-Token", "") == os.environ["EXPORT_TOKEN"]

    def _json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        request = urlsplit(self.path)
        if request.path == "/healthz":
            self._json(200, {"ok": True})
            return
        if not self._authorized():
            self._json(403, {"error": "forbidden"})
            return
        try:
            manifest = _build_export()
            if request.path == "/manifest":
                self._json(200, manifest)
                return
            if request.path != "/chunk":
                self._json(404, {"error": "not_found"})
                return

            query = parse_qs(request.query)
            offset = int(query.get("offset", ["0"])[0])
            length = int(query.get("length", [str(MAX_CHUNK_BYTES)])[0])
            size = EXPORT_PATH.stat().st_size
            if offset < 0 or offset >= size or length < 1:
                self._json(416, {"error": "invalid_range"})
                return
            length = min(length, MAX_CHUNK_BYTES, size - offset)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(length))
            self.end_headers()
            with EXPORT_PATH.open("rb") as source:
                source.seek(offset)
                remaining = length
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except Exception as exc:
            self._json(
                500,
                {"error": "export_failed", "error_type": type(exc).__name__},
            )
            raise


if __name__ == "__main__":
    ThreadingHTTPServer(("", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()
