"""Warm construction and JSON microbenchmarks; no service-load claim or timing gate."""

import gc
import json
import platform
import sys
from collections.abc import Callable
from statistics import median
from timeit import Timer
from typing import Annotated, Any, Literal

from pydantic import BaseModel, TypeAdapter

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


binding = bind_variants([Event, Query, AliasEvent, AliasQuery])


class Envelope(BaseModel):
    messages: list[Annotated[Message[Any], binding]]


family_adapter = build_adapter(Message[Any], variants=[Event, Query, AliasEvent, AliasQuery])
envelope_adapter = TypeAdapter(Envelope)
query = Query(value=1)
wire = family_adapter.dump_json(query)
envelope = Envelope(messages=[Event(value=2), query, AliasQuery(value=3)])
assert family_adapter.validate_json(wire) == query
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
    "family_validate_json": lambda: family_adapter.validate_json(wire),
    "nested_json_roundtrip": lambda: envelope_adapter.validate_json(
        envelope_adapter.dump_json(envelope, warnings="error")
    ),
}
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
            "omit_to_explicit_ratio": {
                name: medians[f"{name}_omit"] / medians[f"{name}_explicit"]
                for name in ("single", "multi", "alias_single", "alias_multi")
            },
        },
        indent=2,
    )
)
