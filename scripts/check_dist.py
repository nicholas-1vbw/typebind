"""Validate distributable metadata and an isolated installation of the built wheel."""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path


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
    args = parser.parse_args()
    distribution_dir = Path(args.dist_dir).resolve()
    index_url: str | None = args.index_url
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
    with tarfile.open(sdist) as archive:
        members = set(archive.getnames())
    for item in (
        "pyproject.toml",
        "README.md",
        "LICENSE",
        "uv.lock",
        ".python-version",
        "src/typebind/py.typed",
        "tests/test_models.py",
        "examples/models.py",
        "examples/decorators.py",
        "examples/manual.py",
        "examples/discovery.py",
        "examples/plugins/core/pyproject.toml",
        "examples/plugins/extra/pyproject.toml",
        "examples/plugins/core/src/typebind_example_core/__init__.py",
        "examples/plugins/extra/src/typebind_example_extra/__init__.py",
        "docs/models.md",
        "docs/behaviors.md",
        "docs/discovery.md",
        "benchmarks/behavior.py",
        "scripts/check_dist.py",
        "scripts/check_release.py",
        "docs/releasing.md",
    ):
        assert f"{name}-{version}/{item}" in members, item

    with tempfile.TemporaryDirectory(prefix="typebind-dist-") as directory:
        temporary = Path(directory)
        environment = temporary / "environment"
        run(uv, "venv", str(environment), "--python", sys.executable, cwd=temporary)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        requirements = temporary / "runtime.txt"
        run(
            uv,
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--no-hashes",
            "--output-file",
            str(requirements),
            cwd=root,
        )
        run(
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "-r",
            str(requirements),
            "--default-index",
            "https://pypi.org/simple/",
            cwd=temporary,
        )
        if index_url is None:
            run(
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--no-deps",
                str(wheel),
                cwd=temporary,
            )
        else:
            # Native index resolution and hash validation; no dependency fallback to TestPyPI.
            candidate = temporary / "candidate.txt"
            digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
            candidate.write_text(f"{name}=={version} --hash=sha256:{digest}\n")
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
                str(candidate),
                cwd=temporary,
            )
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
    print(f"Verified metadata, sdist contents, isolated examples and strict typing from {origin}.")


if __name__ == "__main__":
    main()
