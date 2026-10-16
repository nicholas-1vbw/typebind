"""Validate distributable metadata and an isolated installation of the built wheel."""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

from packaging.requirements import Requirement


def run(*args: str, cwd: Path) -> None:
    """Propagate a failed native tool command without hiding its diagnostics."""
    subprocess.run(args, cwd=cwd, check=True)


def wheel_contents(path: Path) -> dict[str, bytes]:
    """Read packaged files for source and metadata checks."""
    with zipfile.ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist-dir", type=Path, default=Path("dist"))
    parser.add_argument(
        "--index-url", help="Install the expected wheel from this index, without cache"
    )
    parser.add_argument(
        "--constraint", type=Path, help="Use these runtime constraints instead of the lockfile"
    )
    args = parser.parse_args()
    distribution_dir = Path(args.dist_dir).resolve()
    index_url: str | None = args.index_url
    supplied_constraints: Path | None = args.constraint
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    name, version = project["name"], project["version"]
    wheel = distribution_dir / f"{name}-{version}-py3-none-any.whl"
    sdist = distribution_dir / f"{name}-{version}.tar.gz"
    if index_url is not None and not index_url.startswith(("https://", "http://127.0.0.1:")):
        raise ValueError("Use an HTTPS index (or loopback HTTP for local integration tests)")
    uv = shutil.which("uv")
    assert uv is not None, "Run with the repository's uv toolchain on PATH"
    run(sys.executable, "-m", "twine", "check", "--strict", str(wheel), str(sdist), cwd=root)
    contents = wheel_contents(wheel)
    info = f"{name}-{version}.dist-info"
    for source in (root / "src" / name).glob("*"):
        if source.is_file():
            assert contents[f"{name}/{source.name}"] == source.read_bytes(), source
    assert f"{name}/py.typed" in contents
    assert contents[f"{info}/licenses/LICENSE"] == (root / "LICENSE").read_bytes()
    metadata = BytesParser().parsebytes(contents[f"{info}/METADATA"])
    assert metadata["Name"] == name
    assert metadata["Version"] == version
    assert metadata["Requires-Python"] == project["requires-python"]
    assert metadata["License-Expression"] == project["license"]
    assert metadata["Description-Content-Type"] == "text/markdown"
    assert {Requirement(value) for value in metadata.get_all("Requires-Dist", [])} == {
        Requirement(value) for value in project["dependencies"]
    }

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
            (
                "import json, sys, typebind; from pathlib import Path; "
                "from importlib.metadata import distribution, distributions; "
                "assert Path(typebind.__file__).is_relative_to(sys.prefix); "
                "source=json.loads(distribution('typebind').read_text('direct_url.json') or '{}'); "
                "assert not source.get('dir_info', {}).get('editable', False); "
                "assert not any(d.metadata['Name'].startswith('typebind-example-') "
                "for d in distributions())"
            ),
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
