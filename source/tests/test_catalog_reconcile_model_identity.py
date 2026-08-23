from pathlib import Path

REPOSITORY = Path("app/repository.py")
DEPLOY = Path("DEPLOY_PRODUCT_CENTRIC_V0_6_1.sh")


def test_source_products_reuse_the_existing_product_model_identity() -> None:
    source = REPOSITORY.read_text(encoding="utf-8")

    assert "category_id IS NOT DISTINCT FROM %s" in source
    assert "brand_id IS NOT DISTINCT FROM %s" in source
    assert "model IS NOT DISTINCT FROM %s" in source
    assert 'product_id = str(existing_model["product_id"])' in source


def test_deploy_captures_reconcile_failure_before_err_trap() -> None:
    source = DEPLOY.read_text(encoding="utf-8")

    assert 'if gcloud run jobs execute "$RECONCILE_JOB"' in source
    assert 'RECONCILE_RC="$?"' in source
    assert "gcloud run jobs executions tasks describe" in source
