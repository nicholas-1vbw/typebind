"""Warm construction and JSON microbenchmarks; no service-load claim or timing gate."""

import gc
import json
import platform
import sys
from collections.abc import Callable
from statistics import median
from timeit import Timer
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, Field, TypeAdapter, model_validator

from typebind import (
    DiscriminatedModel,
    IdentityDiscriminatedModel,
    bind_variants,
    build_adapter,
    model,
    tag_field,
)


class Message[Kind: str](DiscriminatedModel):
    kind: Kind = tag_field()
    value: int


class Event(Message[Literal["event"]]):
    pass


class Request[Method: str](Message[Literal["request"]]):
    method: Method = tag_field()


class Query(Request[Literal["query"]]):
    pass


type EventTag = Literal["alias-event"]
type QueryTag = Literal["alias-query"]


class AliasEvent(Message[EventTag]):
    pass


class AliasQuery(Request[QueryTag]):
    pass


class IdentityMessage[Kind: str](IdentityDiscriminatedModel):
    kind: Kind = tag_field()
    value: int


class IdentityEvent(IdentityMessage[Literal["event"]]):
    pass


class IdentityRequest[Method: str](IdentityMessage[Literal["request"]]):
    method: Method = tag_field()


class IdentityQuery(IdentityRequest[Literal["query"]]):
    pass


class IdentityAliasEvent(IdentityMessage[EventTag]):
    pass


class IdentityAliasQuery(IdentityRequest[QueryTag]):
    pass


@model(tag="kind")
class DecoratedMessage(DiscriminatedModel):
    value: int


@model(variant="event")
class DecoratedEvent(DecoratedMessage):
    pass


@model(tag="method", variant="request")
class DecoratedRequest(DecoratedMessage):
    pass


@model(variant="query")
class DecoratedQuery(DecoratedRequest):
    pass


@model(tag="kind")
class DecoratedIdentityMessage(IdentityDiscriminatedModel):
    value: int


@model(variant="event")
class DecoratedIdentityEvent(DecoratedIdentityMessage):
    pass


@model(tag="method", variant="request")
class DecoratedIdentityRequest(DecoratedIdentityMessage):
    pass


@model(variant="query")
class DecoratedIdentityQuery(DecoratedIdentityRequest):
    pass


# Native controls have the same field order, frozen tags, and default validation.
class NativeEvent(BaseModel):
    kind: Literal["event"] = Field(default="event", frozen=True, validate_default=True)
    value: int


class NativeQuery(BaseModel):
    kind: Literal["request"] = Field(default="request", frozen=True, validate_default=True)
    value: int
    method: Literal["query"] = Field(default="query", frozen=True, validate_default=True)


class NativeAliasEvent(BaseModel):
    kind: EventTag = Field(default="alias-event", frozen=True, validate_default=True)
    value: int


class NativeAliasQuery(BaseModel):
    kind: Literal["request"] = Field(default="request", frozen=True, validate_default=True)
    value: int
    method: QueryTag = Field(default="alias-query", frozen=True, validate_default=True)


# Semantic controls add the same fields-set guarantee through ordinary validators.
class MarkedEvent(NativeEvent):
    @model_validator(mode="after")
    def mark_identity(self) -> Self:
        self.model_fields_set.update(("kind",))
        return self


class MarkedQuery(NativeQuery):
    @model_validator(mode="after")
    def mark_identity(self) -> Self:
        self.model_fields_set.update(("kind", "method"))
        return self


class MarkedAliasEvent(NativeAliasEvent):
    @model_validator(mode="after")
    def mark_identity(self) -> Self:
        self.model_fields_set.update(("kind",))
        return self


class MarkedAliasQuery(NativeAliasQuery):
    @model_validator(mode="after")
    def mark_identity(self) -> Self:
        self.model_fields_set.update(("kind", "method"))
        return self


binding = bind_variants([IdentityEvent, IdentityQuery, IdentityAliasEvent, IdentityAliasQuery])


class Envelope(BaseModel):
    messages: list[Annotated[IdentityMessage[Any], binding]]


family_adapter = build_adapter(Message[Any], variants=[Event, Query, AliasEvent, AliasQuery])
envelope_adapter = TypeAdapter(Envelope)
query = Query(value=1)
wire = family_adapter.dump_json(query)
envelope = Envelope(
    messages=[IdentityEvent(value=2), IdentityQuery(value=1), IdentityAliasQuery(value=3)]
)
assert family_adapter.validate_json(wire) == query
assert "kind" not in query.model_dump(exclude_unset=True)
assert envelope_adapter.validate_json(envelope_adapter.dump_json(envelope)) == envelope


# Nested families: a field typed as an open family, bound at assembly time,
# compared with a hand-written static discriminated union over the same leaves.
@model(tag="provider")
class Provider(DiscriminatedModel):
    model_id: str


@model(variant="first")
class FirstProvider(Provider):
    effort: str | None = None


@model(variant="second")
class SecondProvider(Provider):
    thinking: bool = False


@model(tag="harness")
class Session(DiscriminatedModel):
    provider: Provider


@model(variant="bound")
class BoundSession(Session):
    pass


@model(tag="harness")
class StaticRoot(DiscriminatedModel):
    provider: Annotated[FirstProvider | SecondProvider, Field(discriminator="provider")]


@model(variant="static")
class StaticSession(StaticRoot):
    pass


bound_adapter = build_adapter(Session, variants=[BoundSession, FirstProvider, SecondProvider])
static_adapter = build_adapter(StaticRoot, variants=[StaticSession])
bound_wire = bound_adapter.dump_json(
    BoundSession(provider=FirstProvider(model_id="m", effort="low"))
)
static_wire = static_adapter.dump_json(
    StaticSession(provider=FirstProvider(model_id="m", effort="low"))
)
bound_session = bound_adapter.validate_json(bound_wire)
static_session = static_adapter.validate_json(static_wire)
assert type(bound_session.provider) is FirstProvider
assert bound_adapter.validate_json(bound_adapter.dump_json(bound_session)) == bound_session

native_query = NativeQuery(value=1)
decorated_query = DecoratedQuery(value=1)
envelope_wire = envelope_adapter.dump_json(envelope)

cases: dict[str, Callable[[], object]] = {
    "copy_plain": lambda: query.model_copy(),
    "copy_payload": lambda: query.model_copy(update={"value": 2}),
    "decorated_copy_payload": lambda: decorated_query.model_copy(update={"value": 2}),
    "native_unchecked_copy_plain": lambda: native_query.model_copy(),
    "native_unchecked_copy_payload": lambda: native_query.model_copy(update={"value": 2}),
    "family_dump_json": lambda: family_adapter.dump_json(query),
    "nested_dump_json": lambda: envelope_adapter.dump_json(envelope),
    "nested_validate_json": lambda: envelope_adapter.validate_json(envelope_wire),
    "decorated_single_omit": lambda: DecoratedEvent(value=1),
    "decorated_multi_omit": lambda: DecoratedQuery(value=1),
    "decorated_identity_single_omit": lambda: DecoratedIdentityEvent(value=1),
    "decorated_identity_multi_omit": lambda: DecoratedIdentityQuery(value=1),
    "single_omit": lambda: Event(value=1),
    "single_explicit": lambda: Event(kind="event", value=1),
    "multi_omit": lambda: Query(value=1),
    "multi_explicit": lambda: Query(kind="request", method="query", value=1),
    "alias_single_omit": lambda: AliasEvent(value=1),
    "alias_single_explicit": lambda: AliasEvent(kind="alias-event", value=1),
    "alias_multi_omit": lambda: AliasQuery(value=1),
    "alias_multi_explicit": lambda: AliasQuery(kind="request", method="alias-query", value=1),
    "identity_single_omit": lambda: IdentityEvent(value=1),
    "identity_single_explicit": lambda: IdentityEvent(kind="event", value=1),
    "identity_multi_omit": lambda: IdentityQuery(value=1),
    "identity_multi_explicit": lambda: IdentityQuery(kind="request", method="query", value=1),
    "identity_alias_single_omit": lambda: IdentityAliasEvent(value=1),
    "identity_alias_single_explicit": lambda: IdentityAliasEvent(kind="alias-event", value=1),
    "identity_alias_multi_omit": lambda: IdentityAliasQuery(value=1),
    "identity_alias_multi_explicit": lambda: IdentityAliasQuery(
        kind="request", method="alias-query", value=1
    ),
    "native_single_omit": lambda: NativeEvent(value=1),
    "native_single_explicit": lambda: NativeEvent(kind="event", value=1),
    "native_multi_omit": lambda: NativeQuery(value=1),
    "native_multi_explicit": lambda: NativeQuery(kind="request", method="query", value=1),
    "native_alias_single_omit": lambda: NativeAliasEvent(value=1),
    "native_alias_single_explicit": lambda: NativeAliasEvent(kind="alias-event", value=1),
    "native_alias_multi_omit": lambda: NativeAliasQuery(value=1),
    "native_alias_multi_explicit": lambda: NativeAliasQuery(
        kind="request", method="alias-query", value=1
    ),
    "marked_single_omit": lambda: MarkedEvent(value=1),
    "marked_single_explicit": lambda: MarkedEvent(kind="event", value=1),
    "marked_multi_omit": lambda: MarkedQuery(value=1),
    "marked_multi_explicit": lambda: MarkedQuery(kind="request", method="query", value=1),
    "marked_alias_single_omit": lambda: MarkedAliasEvent(value=1),
    "marked_alias_single_explicit": lambda: MarkedAliasEvent(kind="alias-event", value=1),
    "marked_alias_multi_omit": lambda: MarkedAliasQuery(value=1),
    "marked_alias_multi_explicit": lambda: MarkedAliasQuery(
        kind="request", method="alias-query", value=1
    ),
    "nested_unset_json_roundtrip": lambda: envelope_adapter.validate_json(
        envelope_adapter.dump_json(envelope, exclude_unset=True, warnings="error")
    ),
    "family_validate_json": lambda: family_adapter.validate_json(wire),
    "nested_family_validate_json": lambda: bound_adapter.validate_json(bound_wire),
    "nested_static_validate_json": lambda: static_adapter.validate_json(static_wire),
    "nested_family_dump_json": lambda: bound_adapter.dump_json(bound_session),
    "nested_static_dump_json": lambda: static_adapter.dump_json(static_session),
    "nested_json_roundtrip": lambda: envelope_adapter.validate_json(
        envelope_adapter.dump_json(envelope, warnings="error")
    ),
}
for custom, native in (
    (Event, NativeEvent),
    (DecoratedEvent, NativeEvent),
    (DecoratedQuery, NativeQuery),
    (Query, NativeQuery),
    (AliasEvent, NativeAliasEvent),
    (AliasQuery, NativeAliasQuery),
):
    assert custom(value=1).model_dump() == native(value=1).model_dump()

for custom, marked in (
    (IdentityEvent, MarkedEvent),
    (DecoratedIdentityEvent, MarkedEvent),
    (DecoratedIdentityQuery, MarkedQuery),
    (IdentityQuery, MarkedQuery),
    (IdentityAliasEvent, MarkedAliasEvent),
    (IdentityAliasQuery, MarkedAliasQuery),
):
    assert custom(value=1).model_dump(exclude_unset=True) == marked(value=1).model_dump(
        exclude_unset=True
    )

results: dict[str, dict[str, float | list[float]]] = {}
medians: dict[str, float] = {}
for name, operation in cases.items():
    timer = Timer(operation, setup="gc.enable()")
    timer.timeit(number=1_000)
    samples = [elapsed / 5_000 * 1e6 for elapsed in timer.repeat(repeat=7, number=5_000)]
    medians[name] = median(samples)
    results[name] = {
        "minimum_us": min(samples),
        "median_us": medians[name],
        "samples_us": samples,
    }
print(
    json.dumps(
        {
            "python": sys.version,
            "platform": platform.platform(),
            "gc_enabled": gc.isenabled(),
            "iterations": 5_000,
            "repeats": 7,
            "scope": (
                "Warm model construction, copy and JSON operations; "
                "excludes assembly, I/O and concurrency."
            ),
            "results": results,
            "decorated_to_native_omitted_ratio": {
                name: medians[f"decorated_{name}_omit"] / medians[f"native_{name}_omit"]
                for name in ("single", "multi")
            },
            "decorated_identity_to_marked_omitted_ratio": {
                name: medians[f"decorated_identity_{name}_omit"] / medians[f"marked_{name}_omit"]
                for name in ("single", "multi")
            },
            "typebind_to_native_omitted_ratio": {
                name: medians[f"{name}_omit"] / medians[f"native_{name}_omit"]
                for name in ("single", "multi", "alias_single", "alias_multi")
            },
            "typebind_to_native_explicit_ratio": {
                name: medians[f"{name}_explicit"] / medians[f"native_{name}_explicit"]
                for name in ("single", "multi", "alias_single", "alias_multi")
            },
            "identity_to_marked_omitted_ratio": {
                name: medians[f"identity_{name}_omit"] / medians[f"marked_{name}_omit"]
                for name in ("single", "multi", "alias_single", "alias_multi")
            },
            "identity_to_marked_explicit_ratio": {
                name: medians[f"identity_{name}_explicit"] / medians[f"marked_{name}_explicit"]
                for name in ("single", "multi", "alias_single", "alias_multi")
            },
            "nested_family_to_static_ratio": {
                name: medians[f"nested_family_{name}"] / medians[f"nested_static_{name}"]
                for name in ("validate_json", "dump_json")
            },
            "omit_to_explicit_ratio": {
                name: medians[f"{name}_omit"] / medians[f"{name}_explicit"]
                for name in ("single", "multi", "alias_single", "alias_multi")
            },
        },
        indent=2,
    )
)
