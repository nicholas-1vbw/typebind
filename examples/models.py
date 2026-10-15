"""Declare tagged models, select variants manually, and round-trip JSON."""

from typing import Any, Literal

from typebind import DiscriminatedModel, IdentityDiscriminatedModel, build_adapter, tag_field


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

# Choose the policy at the family root. Native defaults remain unset.
assert "kind" not in query.model_dump(exclude_unset=True)


class IdentityMessage[Kind: str](IdentityDiscriminatedModel):
    kind: Kind = tag_field()
    identifier: str


class IdentityQuery(IdentityMessage[Literal["query"]]):
    expression: str


identity_adapter = build_adapter(IdentityMessage[Any], variants=(IdentityQuery,))
identity = IdentityQuery(identifier="q", expression="hello")
identity_wire = identity_adapter.dump_json(identity, exclude_unset=True)
assert identity_adapter.validate_json(identity_wire) == identity
assert identity.model_dump() == query.model_dump()
