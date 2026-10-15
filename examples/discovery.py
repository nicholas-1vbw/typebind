"""Discover two installed distributions; no local extras or fallback registrations."""

from typing import Annotated, Any, assert_type

from pydantic import BaseModel, TypeAdapter
from typebind_example_core import Describable, Executable, Message, Query, Request
from typebind_example_extra import Write

from typebind import assemble, bind_variants, discover, select_implementations, select_variants

found = discover(groups={"typebind.example.extensions", "typebind.example.behaviors"})
providers = {
    item.entry_point.dist.metadata["Name"] for item in found if item.entry_point.dist is not None
}
assert providers == {"typebind-example-core", "typebind-example-extra"}
# The shared publication group also contains an unrelated Template family.
variants = select_variants(found, family=Message[Any])
assert set(variants) == {Query, Write}
assert set(select_variants(found, family=Request[Any])) == {Query, Write}
runtime = assemble(
    variants=variants,
    implementations=select_implementations(found, groups={"typebind.example.behaviors"}),
    required_interfaces=(Describable, Executable),
)
binding = bind_variants(variants)


class Envelope(BaseModel):
    messages: list[Annotated[Message[Any], binding]]


adapter = TypeAdapter(Envelope)
document = Envelope(
    messages=[Query(identifier="q", expression="hello"), Write(identifier="w", value=42)]
)
restored = adapter.validate_json(adapter.dump_json(document))
assert restored == document
assert type(restored.messages[0]) is Query
assert type(restored.messages[1]) is Write
for model in restored.messages:
    view = runtime.adapt(model, Describable)
    assert_type(view, Describable)
    assert view.model is model
    print(view.describe(), runtime.adapt(model, Executable).execute())
