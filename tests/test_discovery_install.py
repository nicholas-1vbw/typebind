"""Exercise actual wheel metadata and imports outside the workspace."""

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def plugin_wheels(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Build the shared plugin fixtures once, independent of the library version."""
    uv = shutil.which("uv")
    assert uv is not None, "The repository's uv toolchain must be on PATH"
    root = Path(__file__).resolve().parents[1]
    wheels = tmp_path_factory.mktemp("plugin-wheels")
    for plugin in ("core", "extra"):
        subprocess.run(
            [
                uv,
                "build",
                "--no-sources",
                "--wheel",
                str(root / "examples/plugins" / plugin),
                "--out-dir",
                str(wheels),
            ],
            cwd=root,
            check=True,
        )
    return (
        next(wheels.glob("typebind_example_core-*.whl")),
        next(wheels.glob("typebind_example_extra-*.whl")),
    )


@pytest.mark.integration
@pytest.mark.timeout(120)
def test_installed_plugins_are_discovered_across_distributions(
    tmp_path: Path, plugin_wheels: tuple[Path, Path]
) -> None:
    uv = shutil.which("uv")
    assert uv is not None, "The repository's uv toolchain must be on PATH"
    root = Path(__file__).resolve().parents[1]

    def run(*args: str, cwd: Path = tmp_path, env: dict[str, str] | None = None) -> None:
        result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr

    wheels = tmp_path / "wheels"
    wheels.mkdir()
    first, second = plugin_wheels
    environment = tmp_path / "environment"
    run(uv, "build", "--no-sources", "--wheel", "--out-dir", str(wheels), cwd=root)
    package_wheel = next(wheels.glob("typebind-*.whl"))
    run(uv, "venv", str(environment), "--python", sys.executable)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    constraints = tmp_path / "runtime-constraints.txt"
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
    run(
        uv,
        "pip",
        "install",
        "--python",
        str(python),
        "--constraint",
        str(constraints),
        str(package_wheel),
    )
    run(
        uv,
        "pip",
        "install",
        "--offline",
        "--no-index",
        "--find-links",
        str(wheels),
        "--python",
        str(python),
        str(first),
        str(second),
    )
    # Install these exact checkout artifacts and import both plugins.
    run(
        str(python),
        "-c",
        """
import json
import sys
from pathlib import Path
from importlib.metadata import distribution
import typebind
import typebind_example_core
import typebind_example_extra

assert Path(typebind.__file__).is_relative_to(sys.prefix)
for name, wheel in zip(
    ("typebind", "typebind-example-core", "typebind-example-extra"), sys.argv[1:4], strict=True
):
    source = json.loads(distribution(name).read_text("direct_url.json"))
    assert source["url"] == Path(wheel).as_uri()
    assert not source.get("dir_info", {}).get("editable", False)
assert distribution("typebind").version == sys.argv[4]
""",
        str(package_wheel),
        str(first),
        str(second),
        tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"],
    )

    # One installed consumer covers real entry points, cross-package types,
    # behavior assembly and nested JSON round trips. Generic wheel smoke checks
    # belong to check_dist.py; selection edge cases belong to test_discovery.py.
    example = tmp_path / "discovery.py"
    example.write_text(
        (root / "examples/discovery.py").read_text()
        + """
assert [runtime.adapt(value, Describable).describe() for value in restored.messages] == [
    "hello", "42"
]
assert [runtime.adapt(value, Executable).execute() for value in restored.messages] == ["q", "w"]
"""
    )
    run(str(python), str(example))
    config = tmp_path / "pyrightconfig.json"
    config.write_text(
        '{"typeCheckingMode":"strict","pythonVersion":"3.12","include":["discovery.py"]}'
    )
    run(sys.executable, "-m", "pyright", "--project", str(config), "--pythonpath", str(python))
