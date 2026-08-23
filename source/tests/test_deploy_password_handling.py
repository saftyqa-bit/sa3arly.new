from pathlib import Path


def test_default_deployment_keeps_db_password_out_of_process_arguments():
    wrapper = Path("infra/gcp/deploy.sh").read_text(encoding="utf-8")
    helper = Path("scripts/cloud_sql_user_password.py").read_text(encoding="utf-8")

    assert "deploy_legacy_impl.sh" in wrapper
    assert "scripts/cloud_sql_user_password.py" in wrapper
    assert "printf '%s' \"$DB_PASSWORD\" | python3" in wrapper
    assert "patched = text.replace(unsafe, safe)" in wrapper
    assert "DB password would still appear in argv" in wrapper
    assert "sys.stdin.read()" in helper
    assert "password" not in " ".join(
        line.strip()
        for line in helper.splitlines()
        if "subprocess.run" in line or "gcloud" in line
    ).lower()


def test_legacy_deployment_implementation_is_not_executable():
    legacy = Path("infra/gcp/deploy_legacy_impl.sh")
    assert legacy.exists()
    assert legacy.stat().st_mode & 0o111 == 0
