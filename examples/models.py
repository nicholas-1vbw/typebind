"""Declare tagged models, select variants manually, and round-trip JSON."""

from typing import Any, Literal

from typebind import DiscriminatedModel, build_adapter, tag_field


class Message[Kind: str](DiscriminatedModel):
    kind: Kind = tag_field()
    identifier: str


class Query(Message[Literal["query"]]):
    expression: str


class Ping(Message[Literal["ping"]]):
    pass


adapter = build_adapter(Message[Any], variants=(Query, Ping))
query = Query(identifier="q", expression="hello")  # kind defaults to "query"
wire = adapter.dump_json(query)
restored = adapter.validate_json(wire)
assert type(restored) is Query
assert restored == query
assert type(adapter.validate_python({"kind": "ping", "identifier": "p"})) is Ping
print(wire.decode())
