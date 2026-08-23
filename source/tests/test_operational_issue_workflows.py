from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "workflow",
    [
        "observe-production-data.yml",
        "inspect-catalog-scheduler.yml",
        "diagnose-catalog-typeerror.yml",
        "deploy-production.yml",
    ],
)
def test_operational_workflows_reuse_issue_when_a_run_is_retried(workflow: str):
    source = (
        Path(__file__).parents[1] / ".github" / "workflows" / workflow
    ).read_text(encoding="utf-8")

    assert "gh issue list" in source
    assert "map(select(.title == $title))" in source
    assert "gh issue reopen" in source

