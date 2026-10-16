"""Exercise artifact selection and failures with Python assertions disabled."""

import subprocess
import sys
import zipfile
from pathlib import Path
from runpy import run_path
from types import FunctionType
from typing import cast

import pytest


@pytest.fixture
def index_installer() -> FunctionType:
    script = Path(__file__).resolve().parents[1] / "scripts/check_dist.py"
    return cast(FunctionType, run_path(str(script))["install_index_wheel"])


@pytest.mark.parametrize("failures", [0, 1, 3, 4])
def test_index_installation_retries_are_bounded(
    index_installer: FunctionType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    failures: int,
) -> None:
    attempts: list[tuple[str, ...]] = []
    delays: list[int] = []
    errors: list[subprocess.CalledProcessError] = []

    def install(*args: str, cwd: Path) -> None:
        assert cwd == tmp_path
        attempts.append(args)
        if len(attempts) <= failures:
            print(f"Installer failure {len(attempts)}", file=sys.stderr)
            error = subprocess.CalledProcessError(1, args)
            errors.append(error)
            raise error

    monkeypatch.setitem(index_installer.__globals__, "run", install)
    monkeypatch.setitem(index_installer.__globals__, "sleep", delays.append)
    arguments = ("uv", tmp_path / "python", "https://pypi.org/simple/", tmp_path / "candidate.txt")
    if failures == 4:
        with pytest.raises(subprocess.CalledProcessError) as captured:
            index_installer(*arguments, cwd=tmp_path)
        assert captured.value is errors[-1]
    else:
        index_installer(*arguments, cwd=tmp_path)

    assert len(attempts) == min(failures + 1, 4)
    assert all(attempt == attempts[0] for attempt in attempts)
    assert delays == [10, 20, 40][: min(failures, 3)]
    stderr = capsys.readouterr().err
    for attempt in range(1, failures + 1):
        assert f"Installer failure {attempt}" in stderr
    assert stderr.count("retrying in") == len(delays)


def test_index_installation_does_not_retry_process_start_errors(
    index_installer: FunctionType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    delays: list[int] = []
    error = FileNotFoundError("uv is unavailable")

    def missing(*args: str, cwd: Path) -> None:
        raise error

    monkeypatch.setitem(index_installer.__globals__, "run", missing)
    monkeypatch.setitem(index_installer.__globals__, "sleep", delays.append)
    with pytest.raises(FileNotFoundError) as captured:
        index_installer(
            "uv",
            tmp_path / "python",
            "https://pypi.org/simple/",
            tmp_path / "candidate.txt",
            cwd=tmp_path,
        )
    assert captured.value is error
    assert not delays


def check_dist(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    script = Path(__file__).resolve().parents[1] / "scripts/check_dist.py"
    return subprocess.run(
        [sys.executable, "-O", str(script), "--dist-dir", str(directory), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("count", [0, 2])
def test_requires_an_unambiguous_wheel(tmp_path: Path, count: int) -> None:
    for build in range(count):
        (tmp_path / f"typebind-0.1.0-{build}-py3-none-any.whl").touch()
    result = check_dist(tmp_path)
    assert result.returncode != 0
    assert f"Expected one *.whl artifact in {tmp_path}; found {count}" in result.stderr


def test_rejects_mismatched_release_artifacts(tmp_path: Path) -> None:
    (tmp_path / "typebind-0.1.0-py3-none-any.whl").touch()
    (tmp_path / "typebind-0.2.0.tar.gz").touch()
    result = check_dist(tmp_path)
    assert result.returncode != 0
    assert "Wheel and sdist identify different releases" in result.stderr


@pytest.mark.parametrize(
    ("missing", "message"),
    [
        ("typebind/py.typed", "missing typebind/py.typed"),
        ("typebind-0.1.0a1.dist-info/licenses/LICENSE", "missing the packaged LICENSE"),
    ],
)
def test_required_wheel_files_are_checked_under_optimization(
    tmp_path: Path, missing: str, message: str
) -> None:
    # A build tag and a different Python tag must not require a synthesized filename.
    wheel = tmp_path / "typebind-0.1.0a1-1-py312-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name in ("typebind/py.typed", "typebind-0.1.0a1.dist-info/licenses/LICENSE"):
            if name != missing:
                archive.writestr(name, "fixture")
    (tmp_path / "typebind-0.1.0a1.tar.gz").touch()
    result = check_dist(tmp_path)
    assert result.returncode != 0
    assert message in result.stderr


def test_rejects_artifacts_from_a_different_candidate(tmp_path: Path) -> None:
    (tmp_path / "typebind-0.1.0-py3-none-any.whl").touch()
    (tmp_path / "typebind-0.1.0.tar.gz").touch()
    result = check_dist(tmp_path, "--expected-version", "0.1.0a1")
    assert result.returncode != 0
    assert "Expected release 0.1.0a1; found 0.1.0" in result.stderr
