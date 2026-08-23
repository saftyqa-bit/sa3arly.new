from pathlib import Path


def test_production_price_group_limit_covers_the_current_catalog_without_truncation() -> None:
    deployment = Path("DEPLOY_PRODUCT_CENTRIC_V0_6_1.sh").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/start-price-refresh.yml").read_text(
        encoding="utf-8"
    )

    assert "MAX_URL_GROUPS_PER_RUN=50000" in deployment
    assert 'MAX_URL_GROUPS_PER_RUN: "50000"' in workflow
    assert 'gcloud run jobs update "$control_job"' in workflow
    planner = Path("app/hourly.py").read_text(encoding="utf-8")
    assert 'f"MAX_URL_GROUPS_PER_RUN={settings.max_url_groups_per_run}; refusing "' in planner
    assert '"to truncate coverage silently"' in planner
