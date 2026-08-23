from pathlib import Path


def test_price_run_diagnostics_pages_and_aggregates_every_store() -> None:
    workflow = Path(".github/workflows/inspect-price-run-details.yml").read_text(
        encoding="utf-8"
    )

    assert "run_id:" in workflow
    assert "limit=500&offset=$offset" in workflow
    assert "offset=$((offset + returned))" in workflow
    assert "--retry 8 --retry-all-errors" in workflow
    assert "sleep 1" in workflow
    assert "token_format: id_token" in workflow
    assert "PRICE_RUN_DIAGNOSTIC=" in workflow
    assert '"affected_store_count"' in workflow
    assert '"errors": dict(row["errors"].most_common())' in workflow
    assert "source_url" not in workflow
    assert "INTERNAL_TOKEN" not in workflow
