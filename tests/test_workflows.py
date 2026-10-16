"""Check release routing and permission boundaries without invoking upload jobs."""

from pathlib import Path
from typing import Any, cast

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name: str) -> dict[str, Any]:
    # BaseLoader preserves GitHub's "on" key as text instead of YAML 1.1's boolean.
    return cast(
        dict[str, Any],
        yaml.load((ROOT / ".github/workflows" / name).read_text(), Loader=yaml.BaseLoader),
    )


def test_publish_defaults_to_checks_without_automatic_upload_triggers() -> None:
    config = workflow("publish.yml")
    assert set(config["on"]) == {"workflow_dispatch"}
    target = config["on"]["workflow_dispatch"]["inputs"]["target"]
    assert target["default"] == "check"
    assert set(target["options"]) == {"check", "testpypi", "pypi"}
    assert config["permissions"] == {"contents": "read"}
    jobs = config["jobs"]
    assert jobs["check"]["uses"] == "./.github/workflows/ci.yml"
    assert jobs["build"]["needs"] == "check"
    assert not any("environment" in jobs[job] for job in ("check", "build"))
    privileged = {
        name
        for name, job in cast(dict[str, dict[str, Any]], jobs).items()
        if job.get("permissions", {}).get("id-token") == "write"
    }
    assert privileged == {"testpypi", "pypi"}


@pytest.mark.parametrize("target", ["testpypi", "pypi"])
def test_each_upload_path_is_independent_and_verifies_its_own_artifact(target: str) -> None:
    jobs = workflow("publish.yml")["jobs"]
    upload = jobs[target]
    assert upload["if"] == f"inputs.target == '{target}'"
    assert upload["needs"] == "build"  # Neither index is a dependency of the other.
    assert upload["environment"]["name"] == target
    assert upload["permissions"] == {"id-token": "write"}
    steps = cast(list[dict[str, Any]], upload["steps"])
    assert not any("run" in step or "actions/checkout@" in step.get("uses", "") for step in steps)
    assert steps[0]["with"]["artifact-ids"] == "${{ needs.build.outputs.artifact-id }}"
    assert steps[-1]["uses"].startswith("pypa/gh-action-pypi-publish@")
    verification = jobs[f"verify-{target}"]
    assert set(verification["needs"]) == {"build", target}
    assert "if" not in verification  # Keep native failure/skipped dependency propagation.
    index = "test.pypi.org" if target == "testpypi" else "pypi.org"
    assert f"--index-url https://{index}/simple/" in verification["steps"][-1]["run"]


def test_checkout_never_persists_credentials() -> None:
    for name in ("ci.yml", "publish.yml"):
        jobs = cast(dict[str, dict[str, Any]], workflow(name)["jobs"])
        for job in jobs.values():
            for step in cast(list[dict[str, Any]], job.get("steps", [])):
                if step.get("uses", "").startswith("actions/checkout@"):
                    assert step["with"]["persist-credentials"] == "false"


def test_external_pull_requests_run_only_unprivileged_ci() -> None:
    config = workflow("ci.yml")
    assert set(config["on"]) == {"push", "pull_request", "workflow_dispatch", "workflow_call"}
    assert config["on"]["push"]["branches"] == ["main"]
    assert config["permissions"] == {"contents": "read"}
    jobs = cast(dict[str, dict[str, Any]], config["jobs"])
    for job in jobs.values():
        assert "environment" not in job
        assert "secrets" not in job
        assert job.get("permissions", config["permissions"]) == {"contents": "read"}
        assert job["runs-on"] in {"ubuntu-latest", "${{ matrix.os }}"}
    assert set(jobs["check"]["strategy"]["matrix"]["os"]) == {
        "ubuntu-latest",
        "macos-latest",
        "windows-latest",
    }
