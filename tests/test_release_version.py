"""Verify the release script's source-series and candidate-version contract."""

from collections.abc import Callable
from pathlib import Path
from runpy import run_path
from typing import cast

import pytest
from packaging.version import Version

release_version = cast(
    Callable[[str, str], Version],
    run_path(str(Path(__file__).resolve().parents[1] / "scripts/check_release.py"))[
        "release_version"
    ],
)


@pytest.mark.parametrize("candidate", ["0.1.0.dev1", "0.1.0a1", "0.1.0b1", "0.1.0rc1", "0.1.0"])
def test_accepts_releases_in_the_source_series(candidate: str) -> None:
    assert release_version("0.1.0", candidate) == Version(candidate)


@pytest.mark.parametrize(
    ("base", "candidate"),
    [
        ("0.1", "0.1.0"),
        ("1!0.1.0", "0.1.0"),
        ("0.1.0a1", "0.1.0a1"),
        ("0.01.0", "0.1.0"),
        ("0.1.0", "0.1.0alpha1"),
        ("0.1.0", "1!0.1.0"),
        ("0.1.0", "0.1.0+local"),
        ("0.1.0", "0.1.0.post1"),
        ("0.1.0", "0.2.0a1"),
        ("0.1.0", "0.1.0.1"),
    ],
)
def test_rejects_invalid_source_or_candidate_versions(base: str, candidate: str) -> None:
    with pytest.raises(ValueError):
        release_version(base, candidate)
