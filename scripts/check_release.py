"""Reject uploads unless the selected version tag identifies a commit on main."""

import os
import subprocess
import tomllib
from pathlib import Path

from packaging.version import Version


def git(*args: str) -> str:
    """Resolve repository state through Git rather than parsing its storage."""
    return subprocess.check_output(["git", *args], text=True).strip()


def main() -> None:
    if (
        os.environ.get("GITHUB_EVENT_NAME") == "push"
        and os.environ.get("RELEASE_REF_PROTECTED") != "true"
    ):
        raise ValueError("Production releases require a protected version tag")
    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    expected_ref = f"refs/tags/v{project['version']}"
    if os.environ["GITHUB_REF"] != expected_ref:
        raise ValueError(f"Select the version tag {expected_ref}, not a branch or another version")
    commit = os.environ["GITHUB_SHA"]
    if (
        git("rev-parse", "HEAD") != commit
        or git("rev-parse", f"{expected_ref}^{{commit}}") != commit
    ):
        raise ValueError(
            "The checkout, version tag and workflow commit must identify the same commit"
        )
    subprocess.run(["git", "merge-base", "--is-ancestor", commit, "origin/main"], check=True)
    # GitHub Release metadata is derived from the same project version as the tag.
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a") as stream:
            stream.write(f"prerelease={str(Version(project['version']).is_prerelease).lower()}\n")
    print(f"Verified {expected_ref} at {commit} is on main")


if __name__ == "__main__":
    main()
