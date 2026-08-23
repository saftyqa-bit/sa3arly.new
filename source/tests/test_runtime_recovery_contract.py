import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ensure_price_collection_runtime.sh"


def test_runtime_recovery_script_has_valid_bash_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_runtime_recovers_only_stale_complementary_catalog_runs():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "CATALOG_STALE_REPAIR_CREATED_AT" in source
    assert ".error_codes.superseded_duplicate_run" in source
    assert "(.task_states.queued // 0) > 0" in source
    assert "(.task_states.running // 0) == 0" in source
    assert "stale_repair_epoch >= 3600" in source
    assert 'CATALOG_RUN_STATE=""' in source
    assert "CATALOG_REFRESH=RECOVERING_STALE_COMPLEMENTARY_RUN" in source
