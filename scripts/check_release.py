"""Validate production tag or manual main candidates before an index upload."""

import os
import subprocess
import tomllib
from pathlib import Path

from packaging.version import Version


def git(*args: str) -> str:
    """Resolve repository state through Git rather than parsing its storage."""
    return subprocess.check_output(["git", *args], text=True).strip()


def main() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    version = Version(project["version"])
    ref, commit = os.environ["GITHUB_REF"], os.environ["GITHUB_SHA"]
    if git("rev-parse", "HEAD") != commit:
        raise ValueError("The checkout and workflow must identify the same commit")

    event = os.environ["GITHUB_EVENT_NAME"]
    if event == "push":
        if os.environ.get("RELEASE_REF_PROTECTED") != "true":
            raise ValueError("Production releases require a protected version tag")
        expected_ref = f"refs/tags/v{project['version']}"
        if ref != expected_ref:
            raise ValueError(f"Production requires the version tag {expected_ref}")
        if git("rev-parse", f"{ref}^{{commit}}") != commit:
            raise ValueError("The version tag and workflow must identify the same commit")
    elif event == "workflow_dispatch":
        if ref != "refs/heads/main":
            raise ValueError("TestPyPI rehearsals must be dispatched from main")
    else:
        raise ValueError(f"Unsupported upload event: {event}")

    subprocess.run(["git", "merge-base", "--is-ancestor", commit, "origin/main"], check=True)
    # Both concurrency and Release metadata consume the project's validated version.
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a") as stream:
            stream.write(f"version={version}\nprerelease={str(version.is_prerelease).lower()}\n")
    print(f"Verified {ref} at {commit} is on main (version {version})")


if __name__ == "__main__":
    main()
