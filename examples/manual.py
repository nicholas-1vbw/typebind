"""Manually assemble two models and two interfaces, then round-trip nested data."""

from typing import Annotated, Any, assert_type

from commands import Add, AddObject, Command, Describable, Executable, Uppercase, UppercaseObject
from pydantic import BaseModel, TypeAdapter

from typebind import assemble, bind_variants

variants = (Uppercase, Add)
implementations = (UppercaseObject, AddObject)

runtime = assemble(
    variants=variants,
    implementations=implementations,
    required_interfaces=(Describable, Executable),
)
binding = bind_variants(variants)


class Envelope(BaseModel):
    commands: list[Annotated[Command[Any], binding]]


adapter = TypeAdapter(Envelope)
document = Envelope(
    commands=[Uppercase(identifier="one", text="hello"), Add(identifier="two", left=20, right=22)]
)
restored = adapter.validate_json(adapter.dump_json(document))
assert restored == document
assert type(restored.commands[0]) is Uppercase
assert type(restored.commands[1]) is Add
for command in restored.commands:
    view = runtime.adapt(command, Describable)
    executable = runtime.adapt(command, Executable)
    assert_type(view, Describable)
    assert_type(executable, Executable)
    assert view.model is command and executable.model is command
    print(view.describe())
    print(executable.execute())
