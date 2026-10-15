"""Exercise actual wheel metadata and imports outside the workspace."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.integration
@pytest.mark.timeout(120)
def test_installed_plugins_are_discovered_across_distributions(tmp_path: Path) -> None:
    uv = shutil.which("uv")
    assert uv is not None, "The repository's uv toolchain must be on PATH"
    root = Path(__file__).resolve().parents[1]

    def run(*args: str, cwd: Path = tmp_path, env: dict[str, str] | None = None) -> None:
        result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, check=False)
        assert result.returncode == 0, result.stdout + result.stderr

    wheels = tmp_path / "wheels"
    wheels.mkdir()
    for plugin in ("core", "extra"):
        run(
            uv,
            "build",
            "--no-sources",
            "--wheel",
            str(root / "examples/plugins" / plugin),
            "--out-dir",
            str(wheels),
            cwd=root,
        )
    first = next(wheels.glob("typebind_example_core-*.whl"))
    second = next(wheels.glob("typebind_example_extra-*.whl"))
    environment = tmp_path / "environment"
    run(uv, "build", "--no-sources", "--wheel", "--out-dir", str(wheels), cwd=root)
    package_wheel = next(wheels.glob("typebind-*.whl"))
    run(uv, "venv", str(environment), "--python", sys.executable)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    requirements = tmp_path / "runtime.txt"
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
    run(uv, "pip", "install", "--python", str(python), "-r", str(requirements), str(package_wheel))
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
    script = tmp_path / "consumer.py"
    script.write_text("""from typing import Annotated, Any, Literal
import json
from importlib.metadata import distribution
from pathlib import Path
import sys
import typebind
from pydantic import BaseModel, TypeAdapter
from typebind import (
    DiscriminatedModel, Implementation, assemble, discover, select_implementations,
    bind_variants, build_adapter, discover_variants, select_variants
)
from typebind_example_core import Message, Request, Query, Template, BasicTemplate
from typebind_example_extra import Write
from typebind_example_core import Describable, Executable

assert Path(typebind.__file__).is_relative_to(sys.prefix)
installed_source = json.loads(distribution("typebind").read_text("direct_url.json"))
assert not installed_source.get("dir_info", {}).get("editable", False)
assert installed_source["url"].endswith(".whl")

class Local(Request[Literal["local"]]):
    value: str

found = discover_variants(
    groups=(group for group in ["typebind.example.extensions", "typebind.example.storage"])
)
assert len(found) == 5
assert {item.entry_point.dist.metadata["Name"] for item in found} == {
    "typebind-example-core", "typebind-example-extra"
}
messages = select_variants(found, family=Message[Any], extra_variants=[Local])
assert set(messages) == {Query, Write, Local}
assert set(select_variants(found, family=Request[Any])) == {Query, Write}
assert set(select_variants(found, family=Message[Any], groups={"typebind.example.storage"})) == {
    Query, Write
}
assert select_variants(found, family=Template[Any]) == (BasicTemplate,)
assert set(select_variants(found, family=DiscriminatedModel)) == {Query, Write, BasicTemplate}
assert select_variants(found, family=Query) == (Query,)
assert select_variants(found, family=Request[Literal["query"]], extra_variants=[Local]) == (Query,)
binding = bind_variants(messages)

class Envelope(BaseModel):
    messages: list[Annotated[Message[Any], binding]]
    by_name: dict[str, Annotated[Request[Any], binding]]

local = Local(identifier="l", value="local")
envelope = Envelope(
    messages=[Query(identifier="q", expression="x"), Write(identifier="w", value=1)],
    by_name={"local": local},
)
adapter = TypeAdapter(Envelope)
restored = adapter.validate_json(adapter.dump_json(envelope, warnings="error"))
assert restored == envelope
assert type(restored.messages[0]) is Query
assert type(restored.messages[1]) is Write
assert type(restored.by_name["local"]) is Local
root = build_adapter(Message[Any], variants=messages)
assert type(root.validate_json(root.dump_json(local))) is Local

class LocalObject(Implementation[Local], Describable, Executable):
    def describe(self) -> str:
        return self.model.value
    def execute(self) -> str:
        return self.model.identifier

all_found = discover(groups={"typebind.example.extensions", "typebind.example.behaviors"})
scoped_models = select_variants(all_found, family=Message[Any], extra_variants=[Local])
implementations = select_implementations(all_found, extra_implementations=[LocalObject])
runtime = assemble(
    variants=scoped_models, implementations=implementations,
    required_interfaces=(Describable, Executable),
)
assert runtime.adapt(restored.messages[0], Describable).describe() == "x"
assert runtime.adapt(restored.messages[1], Describable).describe() == "1"
assert runtime.adapt(restored.by_name["local"], Executable).execute() == "l"
assert runtime.adapt(restored.messages[0], Executable).model is restored.messages[0]
""")
    run(str(python), str(script))

    # Resolve types from the installed wheel, including its PEP 561 marker.
    consumer = tmp_path / "typed_consumer.py"
    consumer.write_text(
        (root / "examples/commands.py").read_text()
        + """
from typing import assert_type
from typebind import assemble
value = Uppercase(identifier="q", text="hello")
assert_type(value.kind, Literal["uppercase"])
assert_type(UppercaseObject(value).model, Uppercase)
runtime = assemble(variants=[Uppercase], implementations=[UppercaseObject])
assert_type(runtime.adapt(value, Describable), Describable)
assert_type(runtime.adapt(value, Executable).execute(), str)
"""
    )
    config = tmp_path / "pyrightconfig.json"

    # The documented discovery consumer must work solely with installed distributions.
    example = tmp_path / "discovery.py"
    example.write_text((root / "examples/discovery.py").read_text())
    run(str(python), str(example))
    config.write_text(
        '{"typeCheckingMode":"strict","pythonVersion":"3.12",'
        '"include":["typed_consumer.py","discovery.py"]}'
    )
    run(sys.executable, "-m", "pyright", "--project", str(config), "--pythonpath", str(python))
