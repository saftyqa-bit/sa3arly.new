from pathlib import Path


def test_public_status_exposes_bounded_price_control_failure() -> None:
    repository = Path("app/repository.py").read_text(encoding="utf-8")
    firestore = Path("app/firestore_repository.py").read_text(encoding="utf-8")

    assert "AS latest_price_run_slot" in repository
    assert "WHEN status = 'enqueue_failed'" in repository
    assert "THEN LEFT(metadata ->> 'enqueue_error', 2000)" in repository
    assert "AS latest_price_run_control_error" in repository
    assert '"latest_price_run_slot": None' in firestore
    assert '"latest_price_run_control_error": None' in firestore
