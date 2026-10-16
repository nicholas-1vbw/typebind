"""Validate a release candidate and prepare its version in the build checkout only."""

import os
import subprocess
import tomllib
from pathlib import Path

from packaging.version import Version


def git(*args: str) -> str:
    """Resolve repository state through Git rather than parsing its storage."""
    return subprocess.check_output(["git", *args], text=True).strip()


def release_version(base_text: str, candidate: str) -> Version:
    """Require a canonical version in the source's three-component release series."""
    base = Version(base_text)
    if (
        len(base.release) != 3
        or base.epoch != 0
        or base_text != base.base_version
        or base_text != str(base)
    ):
        raise ValueError("project.version must be a canonical x.y.z base version")
    version = Version(candidate)
    if (
        candidate != str(version)
        or version.epoch != 0
        or version.local is not None
        or version.post is not None
    ):
        raise ValueError("Use a canonical stable, dev, alpha, beta or rc release version")
    if version.release != base.release:
        raise ValueError(f"Release {candidate} must use the source base version {base_text}")
    return version


def main() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    base: str = project["version"]
    ref, commit = os.environ["GITHUB_REF"], os.environ["GITHUB_SHA"]
    if git("rev-parse", "HEAD") != commit:
        raise ValueError("The checkout and workflow must identify the same commit")

    event = os.environ["GITHUB_EVENT_NAME"]
    upload = True
    if event == "push":
        if not ref.startswith("refs/tags/v"):
            raise ValueError("Production requires a v-prefixed version tag")
        candidate = ref.removeprefix("refs/tags/v")
        if git("rev-parse", f"{ref}^{{commit}}") != commit:
            raise ValueError("The version tag and workflow must identify the same commit")
    elif event == "workflow_dispatch":
        target = os.environ["RELEASE_TARGET"]
        if target not in {"check", "testpypi"}:
            raise ValueError(f"Unsupported manual release target: {target}")
        upload = target == "testpypi"
        if upload and ref != "refs/heads/main":
            raise ValueError("TestPyPI rehearsals must be dispatched from main")
        candidate = os.environ.get("RELEASE_VERSION", "")
        if upload and not candidate:
            raise ValueError("TestPyPI requires an explicit version, such as 0.1.0.dev1")
        candidate = candidate or base
    else:
        raise ValueError(f"Unsupported release event: {event}")

    version = release_version(base, candidate)
    if upload:
        subprocess.run(["git", "merge-base", "--is-ancestor", commit, "origin/main"], check=True)
    if candidate != base:
        # uv updates project and local lock metadata without syncing the environment.
        subprocess.run(["uv", "version", candidate, "--no-sync"], check=True)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a") as stream:
            stream.write(
                f"version={version}\nprerelease={str(version.is_prerelease).lower()}\n"
                f"devrelease={str(version.is_devrelease).lower()}\n"
            )
    print(f"Prepared {ref} at {commit}: source base {base}, artifact version {version}")


if __name__ == "__main__":
    main()
