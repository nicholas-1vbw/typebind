"""Exercise release guards and hash-checked installation without publishing."""

import os
import shutil
import subprocess
import sys
import threading
import zipfile
from collections.abc import Generator
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def git(directory: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=directory, text=True).strip()


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-b", "main")
    git(tmp_path, "config", "user.name", "Release Test")
    git(tmp_path, "config", "user.email", "release@example.invalid")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "typebind"\nversion = "0.1.0"\n')
    git(tmp_path, "add", "pyproject.toml")
    git(tmp_path, "-c", "commit.gpgsign=false", "commit", "-m", "Initial version")
    git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(tmp_path, "tag", "v0.1.0")
    return tmp_path


def check_release(
    repository: Path,
    *,
    ref: str = "refs/tags/v0.1.0",
    sha: str | None = None,
    event: str = "workflow_dispatch",
    protected: str = "false",
) -> int:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_release.py")],
        cwd=repository,
        env={
            **os.environ,
            "GITHUB_REF": ref,
            "GITHUB_EVENT_NAME": event,
            "RELEASE_REF_PROTECTED": protected,
            "GITHUB_OUTPUT": str(repository / "release-output"),
            "GITHUB_SHA": sha or git(repository, "rev-parse", "HEAD"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode


def test_version_tag_on_main_is_accepted(repository: Path) -> None:
    assert check_release(repository) == 0
    # Annotated tags resolve to the same commit; later main commits are allowed.
    git(repository, "tag", "-d", "v0.1.0")
    git(repository, "-c", "tag.gpgsign=false", "tag", "-a", "v0.1.0", "-m", "Release")
    release = git(repository, "rev-parse", "HEAD")
    git(repository, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Next commit")
    git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repository, "checkout", "--detach", release)
    assert check_release(repository) == 0


@pytest.mark.parametrize("ref", ["refs/heads/main", "refs/tags/v0.2.0", "refs/tags/0.1.0"])
def test_branch_or_mismatched_version_is_rejected(repository: Path, ref: str) -> None:
    assert check_release(repository, ref=ref) != 0


def test_wrong_workflow_commit_is_rejected(repository: Path) -> None:
    assert check_release(repository, sha="0" * 40) != 0


def test_moved_tag_or_unmerged_commit_is_rejected(repository: Path) -> None:
    git(repository, "checkout", "-b", "unmerged")
    git(repository, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Unmerged")
    assert check_release(repository) != 0  # Tag and checkout differ.
    git(repository, "tag", "-f", "v0.1.0")
    assert check_release(repository) != 0  # Now they agree, but commit is not on main.


@pytest.mark.parametrize("protected", ["false", "", "true"])
def test_tag_push_requires_protection(repository: Path, protected: str) -> None:
    result = check_release(repository, event="push", protected=protected)
    assert (result == 0) is (protected == "true")


@pytest.mark.parametrize(
    ("version", "prerelease"),
    [("0.1.0", "false"), ("0.2.0rc1", "true"), ("0.2.0.dev1", "true"), ("0.1.0.post1", "false")],
)
def test_release_metadata_uses_project_version(
    repository: Path, version: str, prerelease: str
) -> None:
    (repository / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
    git(repository, "add", "pyproject.toml")
    git(repository, "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "Version")
    git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repository, "tag", "-f", f"v{version}")
    assert (
        check_release(repository, ref=f"refs/tags/v{version}", event="push", protected="true") == 0
    )
    assert (repository / "release-output").read_text() == f"prerelease={prerelease}\n"


@contextmanager
def package_index(directory: Path) -> Generator[str, None, None]:
    """Serve only a local fixture; CI never writes to a real package index."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/simple/"
        finally:
            server.shutdown()
            thread.join()


@pytest.mark.integration
@pytest.mark.timeout(180)
def test_index_verification_downloads_expected_wheel_and_rejects_different_bytes(
    tmp_path: Path,
) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    artifacts = tmp_path / "artifacts"
    subprocess.run([uv, "build", "--no-sources", "--out-dir", str(artifacts)], cwd=ROOT, check=True)
    wheel = next(artifacts.glob("*.whl"))
    index = tmp_path / "index/simple/typebind"
    index.mkdir(parents=True)
    served_wheel = index / wheel.name
    shutil.copy2(wheel, served_wheel)
    (index / "index.html").write_text(f'<a href="{wheel.name}">{wheel.name}</a>')
    with package_index(tmp_path / "index") as url:
        command = [
            sys.executable,
            str(ROOT / "scripts/check_dist.py"),
            "--dist-dir",
            str(artifacts),
            "--index-url",
            url,
        ]
        # Invoke with Python directly and no pyright command on PATH. The checker
        # must use the module installed in its own interpreter, not a CLI shim.
        path = os.pathsep.join(
            entry
            for entry in os.environ["PATH"].split(os.pathsep)
            if shutil.which("pyright", path=entry) is None
        )
        # uv and pyright may share an installation directory. Keep the resolved
        # standalone uv binary available without restoring the pyright command.
        tools = tmp_path / "tools"
        tools.mkdir()
        shutil.copy2(uv, tools / Path(uv).name)
        path = os.pathsep.join((str(tools), path))
        assert shutil.which("pyright", path=path) is None
        assert shutil.which("uv", path=path) is not None
        environment = {**os.environ, "PATH": path}
        result = subprocess.run(
            command, cwd=ROOT, env=environment, capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"strict typing from {url}" in result.stdout
        # A valid ZIP comment changes bytes without changing the package/version.
        # The hash must still fail, even after a successful (potentially cached) run.
        with zipfile.ZipFile(served_wheel, "a") as archive:
            archive.comment = b"different release bytes"
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        assert result.returncode != 0
        assert "hash mismatch" in result.stderr.lower(), result.stdout + result.stderr
