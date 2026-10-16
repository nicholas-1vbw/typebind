"""Exercise artifact selection and failures with Python assertions disabled."""

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


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
