"""Validate distributable metadata and an isolated installation of the built wheel."""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from packaging.utils import parse_sdist_filename, parse_wheel_filename
from packaging.version import Version


def run(*args: str, cwd: Path) -> None:
    """Propagate a failed native tool command without hiding its diagnostics."""
    subprocess.run(args, cwd=cwd, check=True)


def artifact(directory: Path, pattern: str) -> Path:
    """Require one unambiguous build output of each distribution format."""
    candidates = sorted(directory.glob(pattern))
    if len(candidates) != 1:
        raise ValueError(
            f"Expected one {pattern} artifact in {directory}; found {len(candidates)}: "
            f"{', '.join(path.name for path in candidates)}"
        )
    return candidates[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument(
        "--index-url", help="Install the expected wheel from this index, without cache"
    )
    parser.add_argument(
        "--constraint", type=Path, help="Use these runtime constraints instead of the lockfile"
    )
    parser.add_argument("--expected-version", type=Version, help="Require this release version")
    args = parser.parse_args()
    distribution_dir = Path(args.dist_dir).resolve()
    index_url: str | None = args.index_url
    supplied_constraints: Path | None = args.constraint
    root = Path(__file__).resolve().parents[1]
    wheel = artifact(distribution_dir, "*.whl")
    sdist = artifact(distribution_dir, "*.tar.gz")
    name, version, _, _ = parse_wheel_filename(wheel.name)
    if parse_sdist_filename(sdist.name) != (name, version):
        raise ValueError(f"Wheel and sdist identify different releases: {wheel.name}, {sdist.name}")
    if args.expected_version is not None and version != args.expected_version:
        raise ValueError(f"Expected release {args.expected_version}; found {version}")
    if index_url is not None and not index_url.startswith(("https://", "http://127.0.0.1:")):
        raise ValueError("Use an HTTPS index (or loopback HTTP for local integration tests)")
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("Run with the repository's uv toolchain on PATH")
    with zipfile.ZipFile(wheel) as archive:
        files = set(archive.namelist())
        if "typebind/py.typed" not in files:
            raise ValueError(f"{wheel.name} is missing typebind/py.typed")
        if not any(path.endswith(".dist-info/licenses/LICENSE") for path in files):
            raise ValueError(f"{wheel.name} is missing the packaged LICENSE")
    run(sys.executable, "-m", "twine", "check", "--strict", str(wheel), str(sdist), cwd=root)

    with tempfile.TemporaryDirectory(prefix="typebind-dist-") as directory:
        temporary = Path(directory)
        environment = temporary / "environment"
        run(uv, "venv", str(environment), "--python", sys.executable, cwd=temporary)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        constraints = (
            supplied_constraints.resolve()
            if supplied_constraints is not None
            else temporary / "runtime-constraints.txt"
        )
        if supplied_constraints is None:
            run(
                uv,
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--no-hashes",
                "--output-file",
                str(constraints),
                cwd=root,
            )
        if index_url is None:
            candidate = str(wheel)
        else:
            # Native index resolution and hash validation; no dependency fallback to TestPyPI.
            requirement = temporary / "candidate.txt"
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            requirement.write_text(f"{name}=={version} --hash=sha256:{digest}\n")
            run(
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--no-deps",
                "--no-cache",
                "--only-binary",
                ":all:",
                "--index-url",
                index_url,
                "--require-hashes",
                "-r",
                str(requirement),
                cwd=temporary,
            )
            # Resolve dependencies from the hash-verified installed wheel's metadata.
            candidate = f"{name}=={version}"
        run(
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--constraint",
            str(constraints),
            "--default-index",
            "https://pypi.org/simple/",
            candidate,
            cwd=temporary,
        )
        run(uv, "pip", "check", "--python", str(python), cwd=temporary)
        # Copy the public example into a neutral consumer directory: no checkout on sys.path.
        for example in (root / "examples").glob("*.py"):
            shutil.copy2(example, temporary / example.name)
        run(
            str(python),
            "-I",
            "-c",
            """
import json
import sys
from pathlib import Path
from importlib.metadata import distribution, distributions
import typebind

if not Path(typebind.__file__).is_relative_to(sys.prefix):
    raise RuntimeError("typebind was imported from outside the isolated environment")
source = json.loads(distribution('typebind').read_text('direct_url.json') or '{}')
if source.get('dir_info', {}).get('editable', False):
    raise RuntimeError("typebind was installed as an editable checkout")
if any(d.metadata['Name'].startswith('typebind-example-') for d in distributions()):
    raise RuntimeError("Local plugin fixtures leaked into the isolated environment")
""",
            cwd=temporary,
        )
        for example in ("models.py", "manual.py", "decorators.py"):
            run(str(python), str(temporary / example), cwd=temporary)
        (temporary / "pyrightconfig.json").write_text(
            '{"typeCheckingMode": "strict", "pythonVersion": "3.12", '
            '"include": ["models.py", "manual.py", "decorators.py", "commands.py"]}'
        )
        run(
            sys.executable,
            "-m",
            "pyright",
            "--pythonpath",
            str(python),
            "--project",
            str(temporary / "pyrightconfig.json"),
            cwd=temporary,
        )
    origin = index_url or "local artifacts"
    print(
        "Verified metadata, dependency installation, isolated examples "
        f"and strict typing from {origin}."
    )


if __name__ == "__main__":
    main()
