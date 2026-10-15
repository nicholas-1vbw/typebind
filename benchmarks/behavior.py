"""Warm single-process microbenchmark, not a service throughput/load test."""

import gc
import json
import platform
import sys
from abc import abstractmethod
from statistics import median
from timeit import Timer
from typing import Any, Literal, Protocol, get_type_hints

from typebind import DiscriminatedModel, Implementation, assemble, tag_field


class Message[Kind: str](DiscriminatedModel):
    kind: Kind = tag_field()
    text: str


class Query(Message[Literal["query"]]):
    pass


class Describable(Protocol):
    @property
    @abstractmethod
    def model(self) -> Message[Any]: ...

    @abstractmethod
    def describe(self) -> str: ...


class QueryObject(Implementation[Query], Describable):
    def describe(self) -> str:
        return self.model.text


runtime = assemble(
    variants=[Query], implementations=[QueryObject], required_interfaces=[Describable]
)
query = Query(text="hello")
view = runtime.adapt(query, Describable)


def direct(model: Query) -> str:
    return model.text


def reflecting_call() -> str:
    """Comparison only: simulate repeated family annotation resolution."""
    member = vars(Describable)["model"]
    get_type_hints(member.fget)
    return runtime.adapt(query, Describable).describe()


cases = {
    "direct_function": "direct(query)",
    "reuse_adapter": "view.describe()",
    "known_constructor_and_call": "QueryObject(query).describe()",
    "adapt_and_call": "runtime.adapt(query, Describable).describe()",
    "reflection_and_adapt_and_call": "reflecting_call()",
}
assert direct(query) == view.describe() == reflecting_call() == "hello"
results: dict[str, object] = {}
for name, statement in cases.items():
    timer = Timer(statement, setup="gc.enable()", globals=globals())
    timer.timeit(number=1_000)
    samples = [elapsed / 100_000 * 1e6 for elapsed in timer.repeat(repeat=7, number=100_000)]
    results[name] = {
        "minimum_us": min(samples),
        "median_us": median(samples),
        "samples_us": samples,
    }
print(
    json.dumps(
        {
            "python": sys.version,
            "platform": platform.platform(),
            "gc_enabled": gc.isenabled(),
            "iterations": 100_000,
            "repeats": 7,
            "scope": (
                "Sequential warm calls on an existing model; excludes discovery, "
                "assembly, parsing, I/O and concurrency."
            ),
            "results": results,
        },
        indent=2,
    )
)
