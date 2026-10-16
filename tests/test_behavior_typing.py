"""Verify actual downstream inference and rejection of invalid behavior contracts."""

import json
import subprocess
import sys
from pathlib import Path

CONSUMER = """
from typing import assert_type
from typebind import (
    BehaviorAdapter, DiscoveredVariant, DiscoveredImplementation,
    assemble, discover, select_implementations, select_variants,
)

upper = Uppercase(identifier="q", text="hello")
assert_type(UppercaseObject(upper).model, Uppercase)
found = discover(groups={"example.command.models", "example.command.behaviors"})
assert_type(found, tuple[DiscoveredVariant | DiscoveredImplementation, ...])
variants = select_variants(found, family=Command[Any], extra_variants=[Uppercase, Add])
implementations = select_implementations(found, extra_implementations=[UppercaseObject, AddObject])
assert_type(implementations, tuple[type[Implementation[Any]], ...])
runtime = assemble(
    variants=variants, implementations=implementations,
    required_interfaces=[Describable, Executable],
)
assert_type(runtime, BehaviorAdapter)
view = runtime.adapt(upper, Describable)
assert_type(view, Describable)
assert_type(view.model, Command[Any])
assert_type(view.describe(), str)
assert_type(runtime.adapt(upper, Executable).execute(), str)
"""

INVALID = """
UppercaseObject(Add(identifier="a", left=1, right=2))  # reject
view.execute()  # reject
runtime.adapt(upper, Describable).model.text  # reject
runtime.adapt(upper, "Describable")  # reject
runtime.adapt(upper, Describable | Executable)  # reject
class BadResult(Implementation[Uppercase], Describable):
    def describe(self) -> int:  # reject
        return 1
class Other(DiscriminatedModel):
    value: int
class WrongFamily(Implementation[Other], Describable):  # reject
    def describe(self) -> str:
        return str(self.model.value)
class Missing(Implementation[Uppercase], Executable):
    pass
Missing(upper)  # reject
"""


def test_strict_behavior_consumers(tmp_path: Path) -> None:
    (tmp_path / "pyrightconfig.json").write_text(
        json.dumps(
            {
                "typeCheckingMode": "strict",
                "pythonVersion": "3.12",
            }
        )
    )
    example = Path(__file__).resolve().parents[1] / "examples/commands.py"
    source = example.read_text() + CONSUMER
    sample = tmp_path / "consumer.py"
    sample.write_text(source)
    command = [
        sys.executable,
        "-m",
        "pyright",
        "--project",
        str(tmp_path),
        "--pythonpath",
        sys.executable,
        "--outputjson",
    ]
    positive = subprocess.run(command, capture_output=True, text=True, check=False)
    assert positive.returncode == 0, positive.stdout + positive.stderr
    sample.write_text(source + INVALID)
    negative = subprocess.run(command, capture_output=True, text=True, check=False)
    assert negative.returncode != 0
    diagnostics = json.loads(negative.stdout)["generalDiagnostics"]
    errors = [item for item in diagnostics if item["severity"] == "error"]
    offset = len(source.splitlines())
    for number, line in enumerate(INVALID.splitlines()):
        if "# reject" in line:
            assert any(item["range"]["start"]["line"] == offset + number for item in errors), errors
    assert all(item["range"]["start"]["line"] >= offset for item in errors)
