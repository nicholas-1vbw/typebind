# Interfaces and behavior assembly

[Back to README](../README.md). See the [manual assembly example](../examples/README.md#models-and-manual-assembly)
for two models and two interfaces with no discovery calls. The snippets below use
`Message`, `Query`, and `query` from [the model guide](models.md).

## Behavior assembly

Data models and behavior implementations remain separate classes. An interface
uses a native `Protocol` with a read-only `model` property whose return annotation
is its authoritative family binding. Use `@abstractmethod` for required methods:
Python then rejects missing implementations, including when the class is loaded
from an untyped plugin. Method signature compatibility is checked by Pyright;
assembly is not a runtime type checker for arbitrary plugin code.

```python
from abc import abstractmethod
from typing import Any, Protocol
from typebind import Implementation, assemble


class Describable(Protocol):
    @property
    @abstractmethod
    def model(self) -> Message[Any]: ...

    @abstractmethod
    def describe(self) -> str: ...


class QueryObject(Implementation[Query], Describable):
    def describe(self) -> str:
        return self.model.expression


runtime = assemble(
    variants=(Query,),
    implementations=(QueryObject,),
    required_interfaces=(Describable,),
)
query_view = runtime.adapt(query, Describable)
assert query_view.model is query
assert query_view.describe() == query.expression
```

`Implementation[T]` is an ordinary native generic class, not a model, generated
mixin, metaclass, or class-creation hook. It stores the original model reference.
Its read-only property allows a concrete model return type to satisfy its
family interface. One implementation can implement multiple interfaces, even
when those interfaces bind to different ancestor families. Native generic
forwarding through intermediate implementation bases is supported; there must
ultimately be one concrete model target. Models do not inherit these interfaces.
This includes forwarding a tag parameter inside a model: a base such as
`Shared[K: str](Implementation[Message[K]])` specialized as
`Shared[Literal["query"]]` targets `Message[Literal["query"]]`. Assembly uses
Pydantic's native model specialization; the target retains its exact class identity.

Assembly resolves the `model` property's return annotation with `get_type_hints`.
With `from __future__ import annotations`, a family defined only inside a function
may be unavailable to that lookup, causing `NameError` even if it was in scope
when the Protocol was declared. Define the family and interface at module scope
so the annotation's names remain available during assembly.

Both `variants` and `implementations` are ordinary tuples. No discovery or
publication metadata is needed. Use [entry point discovery](discovery.md) when
other packages should supply these tuples.

### Assembly contract and hot path

`assemble(*, variants, implementations, required_interfaces=())` returns a
`BehaviorAdapter`. It resolves declarations, checks concrete targets, checks
interface families, rejects abstract implementations, and verifies that each
constructor accepts one positional model. It does not run constructors. Optional
constructor arguments may have defaults; dependency injection is not provided.

Only implementation targets in the selected variants enter the dispatch map.
Conflicts for other concrete targets do not affect that scope. Within a scope,
repeating the same implementation class is harmless; different implementations
of the same `(concrete model, interface)` pair raise `ValueError`, including
interfaces inherited from parent Protocols. No discovery-order precedence exists.
Each required interface must be covered for every selected variant that belongs
to that interface's family. Missing coverage raises `ValueError`. Requirements
for unrelated families do not impose obligations on other models. Data-tag
conflicts remain owned by `bind_variants` / `build_adapter`.

The resulting read-only dispatch map is a derived snapshot. There is no mutable
global registry, periodic discovery, object cache, or automatic update when
classes change. Create a new assembly for a new scope/declaration set.

`adapt(model, Interface)` performs exact type/interface lookup and creates a new
implementation instance. It does not repeat reflection, family checks, Pydantic
validation, or plugin discovery. There is no implicit fallback to an adapter for
a model's ancestor: an out-of-scope subtype cannot bypass selection. Missing pairs
raise `LookupError`; constructor exceptions propagate unchanged. Two adaptations
of one model may create two objects, both retaining that original model.

Interfaces are explicit native Protocol class objects, and implementations
explicitly inherit them. Orthogonal Protocols without a `model` member may coexist
on implementations and are not registered as family-bound behaviors. Arbitrary
structural matches, union/alias type-form
objects as runtime interface keys, and automatic dependency injection are not
part of this API. The `type[I]` parameter preserves the return type `I` in consumer
code and rejects string and union arguments; it does not statically correlate
the two arguments' families. The internal
heterogeneous lookup uses a local cast after assembly checks. Generic field
access and method signatures remain statically checked, including wrong-family
implementation declarations. No `cast` is needed by consumers.

### Performance verification

After `uv sync --locked`, the `uv run --no-sync poe benchmark` task runs a standard-library microbenchmark
of direct functions, prebound adapter methods, known adapter construction,
assembled adaptation, and a reflection-on-every-call comparison. It reports raw
samples and minimum/median microseconds, with GC enabled. It excludes discovery,
assembly, model parsing, I/O, and concurrency, so it is not a service QPS or tail
latency claim. No machine-dependent timing threshold gates the tests. A separate
correctness test disables reflection/discovery/validation after assembly and
requires adaptation to keep working.
