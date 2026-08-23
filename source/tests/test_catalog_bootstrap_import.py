from __future__ import annotations

import json
from pathlib import Path

from scripts.import_catalog_bootstrap import read_records


def test_importer_reads_uws_json_wrapper(tmp_path: Path) -> None:
    path = tmp_path / "export.json"
    path.write_text(
        json.dumps({"rows": [{"PAGE URL": "https://shop.example/products/a"}]}),
        encoding="utf-8",
    )

    assert read_records(path) == [{"PAGE URL": "https://shop.example/products/a"}]


def test_importer_reads_csv_with_uws_column_names(tmp_path: Path) -> None:
    path = tmp_path / "export.csv"
    path.write_text(
        'PAGE URL,Name,Price\nhttps://shop.example/products/a,"Product A",99\n',
        encoding="utf-8",
    )

    assert read_records(path) == [
        {
            "PAGE URL": "https://shop.example/products/a",
            "Name": "Product A",
            "Price": "99",
        }
    ]


def test_catalog_import_workflow_keeps_production_secrets_out_of_github() -> None:
    workflow = Path(".github/workflows/import-catalog-bootstrap.yml").read_text(
        encoding="utf-8"
    )

    assert "gcloud secrets versions access latest" not in workflow
    assert "INTERNAL_TOKEN_SECRET" not in workflow
    assert "INTERNAL_TOKEN:" not in workflow
    assert "--set-secrets=\"DATABASE_URL=${DB_SECRET}:latest\"" in workflow
    assert "--service-account=\"$BOOTSTRAP_SERVICE_ACCOUNT\"" in workflow
    assert "--no-allow-unauthenticated" in workflow
    assert "token_format: id_token" in workflow
    assert "gcloud run services delete" in workflow
    assert "preview = ingest_catalog_bootstrap(build_request(True))" in workflow
    assert '--data-binary "@$batch_file"' in workflow
    assert "download_url must use HTTPS" in workflow


def test_catalog_import_workflow_normalizes_csv_before_preflight() -> None:
    workflow = Path(".github/workflows/import-catalog-bootstrap.yml").read_text(
        encoding="utf-8"
    )

    assert 'catalog-export.raw' in workflow
    assert "csv.DictReader" in workflow
    assert "BATCH_SIZE = 500" in workflow
    assert 'catalog-batches' in workflow
    assert 'catalog-export-manifest.json' in workflow
    assert 'python3 -m json.tool "$RUNNER_TEMP/catalog-export.json"' not in workflow


def test_catalog_import_workflow_preflights_all_batches_before_commit() -> None:
    workflow = Path(".github/workflows/import-catalog-bootstrap.yml").read_text(
        encoding="utf-8"
    )

    assert '"/import", "/import/commit"' in workflow
    assert '"$IMPORTER_URL/import")"' in workflow
    assert 'if [[ "$DRY_RUN" != "true" ]]' in workflow
    assert '"$IMPORTER_URL/import/commit")"' in workflow
    assert 'preflight batch count mismatch' in workflow
    assert 'stats["rows_received"] != manifest["rows"]' in workflow
