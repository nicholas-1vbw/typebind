"""Decorator declarations keep payload typing and share the existing assembly APIs."""

from abc import abstractmethod
from typing import Annotated, Protocol, assert_type

from pydantic import BaseModel

from typebind import (
    IdentityDiscriminatedModel,
    Implementation,
    assemble,
    bind_variants,
    model,
)


@model(tag="kind")
class Message(IdentityDiscriminatedModel):
    identifier: str


@model(tag="method", variant="request")
class Request(Message):
    sequence: int = 0


@model(variant="query")
class Query(Request):
    text: str


@model(variant="write")
class Write(Request):
    value: int


class Describable(Protocol):
    @property
    @abstractmethod
    def model(self) -> Request: ...

    @abstractmethod
    def describe(self) -> str: ...


class QueryObject(Implementation[Query], Describable):
    def describe(self) -> str:
        return f"Query {self.model.text!r}"


class WriteObject(Implementation[Write], Describable):
    def describe(self) -> str:
        return f"Write {self.model.value}"


variants = (Query, Write)
binding = bind_variants(variants)
runtime = assemble(
    variants=variants,
    implementations=(QueryObject, WriteObject),
    required_interfaces=(Describable,),
)


class Envelope(BaseModel):
    requests: list[Annotated[Request, binding]]


query = Query(identifier="q", text="hello")
assert_type(query.text, str)
assert_type(query.identifier, str)
assert_type(query.sequence, int)
copied_query = query.model_copy(update={"text": "copied"})
assert_type(copied_query, Query)
assert copied_query.model_dump()["method"] == "query"
try:
    query.model_copy(update={"method": "query"})
except ValueError:
    pass  # Even writing the same bound tag through model_copy is rejected.
else:
    raise AssertionError("model_copy must reject bound tag updates")
envelope = Envelope(requests=[copied_query, Write(identifier="w", value=42)])
wire = envelope.model_dump_json(exclude_unset=True)
restored = Envelope.model_validate_json(wire)
assert restored == envelope
assert type(restored.requests[0]) is Query
assert type(restored.requests[1]) is Write
for request in restored.requests:
    view = runtime.adapt(request, Describable)
    assert_type(view, Describable)
    print(view.describe())
print(wire)
