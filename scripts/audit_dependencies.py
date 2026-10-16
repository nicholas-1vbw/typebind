"""Export the project lock with uv and audit it with pip-audit's native reader."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    """Fail on export, collection, service errors or known vulnerabilities."""
    root = Path(__file__).resolve().parents[1]
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("Run with the repository's uv toolchain on PATH")
    with tempfile.TemporaryDirectory(prefix="typebind-audit-") as directory:
        subprocess.run(
            [
                uv,
                "export",
                "--locked",
                "--all-groups",
                "--no-emit-local",
                "--format",
                "pylock.toml",
                "--output-file",
                str(Path(directory) / "pylock.toml"),
            ],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [sys.executable, "-m", "pip_audit", "--locked", directory, "--strict"],
            cwd=root,
            check=True,
        )


if __name__ == "__main__":
    main()
