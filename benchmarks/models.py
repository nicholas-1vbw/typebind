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

from typebind import DiscriminatedModel, bind_variants, build_adapter, tag_field


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


binding = bind_variants([Event, Query, AliasEvent, AliasQuery])


class Envelope(BaseModel):
    messages: list[Annotated[Message[Any], binding]]


family_adapter = build_adapter(Message[Any], variants=[Event, Query, AliasEvent, AliasQuery])
envelope_adapter = TypeAdapter(Envelope)
query = Query(value=1)
wire = family_adapter.dump_json(query)
envelope = Envelope(messages=[Event(value=2), query, AliasQuery(value=3)])
assert family_adapter.validate_json(wire) == query
assert family_adapter.validate_json(family_adapter.dump_json(query, exclude_unset=True)) == query
assert envelope_adapter.validate_json(envelope_adapter.dump_json(envelope)) == envelope

cases: dict[str, Callable[[], object]] = {
    "single_omit": lambda: Event(value=1),
    "single_explicit": lambda: Event(kind="event", value=1),
    "multi_omit": lambda: Query(value=1),
    "multi_explicit": lambda: Query(kind="request", method="query", value=1),
    "alias_single_omit": lambda: AliasEvent(value=1),
    "alias_single_explicit": lambda: AliasEvent(kind="alias-event", value=1),
    "alias_multi_omit": lambda: AliasQuery(value=1),
    "alias_multi_explicit": lambda: AliasQuery(kind="request", method="alias-query", value=1),
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
    "nested_json_roundtrip": lambda: envelope_adapter.validate_json(
        envelope_adapter.dump_json(envelope, warnings="error")
    ),
}
for custom, native in (
    (Event, NativeEvent),
    (Query, NativeQuery),
    (AliasEvent, NativeAliasEvent),
    (AliasQuery, NativeAliasQuery),
):
    assert custom(value=1).model_dump() == native(value=1).model_dump()

for custom, marked in (
    (Event, MarkedEvent),
    (Query, MarkedQuery),
    (AliasEvent, MarkedAliasEvent),
    (AliasQuery, MarkedAliasQuery),
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
                "Warm model construction and JSON operations; "
                "excludes assembly, I/O and concurrency."
            ),
            "results": results,
            "typebind_to_native_omitted_ratio": {
                name: medians[f"{name}_omit"] / medians[f"native_{name}_omit"]
                for name in ("single", "multi", "alias_single", "alias_multi")
            },
            "typebind_to_native_explicit_ratio": {
                name: medians[f"{name}_explicit"] / medians[f"native_{name}_explicit"]
                for name in ("single", "multi", "alias_single", "alias_multi")
            },
            "typebind_to_marked_omitted_ratio": {
                name: medians[f"{name}_omit"] / medians[f"marked_{name}_omit"]
                for name in ("single", "multi", "alias_single", "alias_multi")
            },
            "typebind_to_marked_explicit_ratio": {
                name: medians[f"{name}_explicit"] / medians[f"marked_{name}_explicit"]
                for name in ("single", "multi", "alias_single", "alias_multi")
            },
            "omit_to_explicit_ratio": {
                name: medians[f"{name}_omit"] / medians[f"{name}_explicit"]
                for name in ("single", "multi", "alias_single", "alias_multi")
            },
        },
        indent=2,
    )
)
