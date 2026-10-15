# Entry point discovery

[Back to README](../README.md). Discovery supplies classes to the same
`build_adapter`, `bind_variants`, and `assemble` APIs used by manual composition.
It is optional; installing a plugin does not change an existing adapter.

The core plugin example uses `IdentityDiscriminatedModel` for messages and
`DiscriminatedModel` for templates. Discovery and behavior assembly accept both
through their shared base; the choice only changes automatic identity marking
and unset exports. See [model export policies](models.md#choosing-export-semantics).

## Run the cross-package example

From the repository root:

```sh
uv run --locked poe example-discovery
```

The root development group installs two local example distributions through
native `tool.uv.sources` path sources. They are not runtime dependencies of
`typebind`, and they are not included in its wheel. There is no extra workspace
or per-example lockfile. The examples use ordinary installed entry point metadata:

| Distribution | Contents |
| --- | --- |
| [typebind-example-core](../examples/plugins/core/pyproject.toml) | Defines the `Message` / `Request` and `Template` families and interfaces; publishes `Query`, `BasicTemplate`, and `QueryObject` |
| [typebind-example-extra](../examples/plugins/extra/pyproject.toml) | Publishes `Write(Request[Literal["write"]])` and `WriteObject`, importing the shared contract from core |

[The consumer](../examples/discovery.py) makes one batch discovery call, selects
both top-level and intermediate families, assembles the two interfaces, and
round-trips a nested envelope. It asserts that both distributions were found;
it supplies no `extra_variants` or `extra_implementations`. Missing plugins fail
instead of silently falling back to local classes.

The installed-distribution test builds these same projects into wheels and
installs them with a `typebind` wheel in a clean environment. It runs the consumer
outside the checkout and checks its types against the installed packages.

To use this pattern in another project, put the family and interface contracts in
a shared package, declare dependencies on that package from its extensions, and
publish exports in each extension's `project.entry-points`. Install the providing
packages before running discovery. Group names are conventions chosen by the
application and its plugin authors; typebind does not reserve them.

## Discovery and selection reference

The reference snippets below use the declarations from [models.md](models.md).
The mixed behavior example additionally uses `Describable` and `QueryObject`
from [behaviors.md](behaviors.md).

## Entry point discovery and source scopes

Groups and families are independent. An entry point group is a cross-package
publication convention; a family is a model's inheritance relationship. A group
can publish multiple families, and a family's implementations can be spread
across multiple groups and distributions. Models do not declare group names.

Publish one model class per entry point in the providing package's `pyproject.toml`:

```toml
[project.entry-points."example.extensions"]
query = "example_plugin.models:Query"
template = "example_plugin.models:BasicTemplate"

[project.entry-points."example.storage"]
write = "example_plugin.models:Write"
```

The target classes must exist in the installed plugin package. Entry names such
as `query` are publication labels, not discriminator values. Tags come only from
the model's Pydantic fields. Another package may publish into the same groups,
including with the same entry names; discovery retains each source record.

Discover all needed groups in one batch, then reuse the result for different
family and source selections. This example uses the model declarations above;
`Query` also demonstrates an explicitly supplied built-in implementation:

```python
from typebind import discover_variants, select_variants


class LocalQuery(Request[Literal["local"]]):
    expression: str


core_groups = {"example.core"}
extension_groups = {"example.extensions", "example.storage"}
discovered = discover_variants(groups=core_groups | extension_groups)

message_variants = select_variants(
    discovered,
    family=Message[Any],
    extra_variants=[Query, LocalQuery],
)
storage_requests = select_variants(
    discovered,
    family=Request[Any],
    groups={"example.storage"},
    extra_variants=[LocalQuery],
)

message_adapter = build_adapter(Message[Any], variants=message_variants)
discovered_binding = bind_variants(message_variants)


class DiscoveredEnvelope(BaseModel):
    messages: list[Annotated[Message[Any], discovered_binding]]
    by_name: dict[str, Annotated[Request[Any], discovered_binding]]


local = LocalQuery(identifier="local", sequence=2, expression="hello")
envelope = DiscoveredEnvelope(messages=[query, local], by_name={"local": local})
restored_envelope = DiscoveredEnvelope.model_validate_json(envelope.model_dump_json())
assert restored_envelope == envelope
assert type(restored_envelope.messages[1]) is LocalQuery
assert type(restored_envelope.by_name["local"]) is LocalQuery
```

`discover_variants(*, groups, include=None)` returns a tuple of
`DiscoveredVariant(model, entry_point)` records. The record is a native
`NamedTuple`; `entry_point` is the original `importlib.metadata.EntryPoint`,
including its group, name, target value, and providing distribution. A nonempty
batch calls `importlib.metadata.entry_points()` once and traverses that collection
once. Each selected entry record is loaded once. Different records pointing at
the same class are still distinct publications; discovery does not globally
cache objects or metadata. Empty groups return immediately, and absent groups
produce no results. This is not a guarantee about internal filesystem scans.

Both discovery and selection accept `groups: Iterable[str]`, including generators.
Each provided iterable is consumed once to capture the group names. A single
string such as `groups="example.extensions"` raises `TypeError`; use
`groups={"example.extensions"}` instead. Strings are not interpreted as character
scopes, and invalid string scopes fail before enumeration, loading, or selection.

A scope is the caller's source selection, expressed with ordinary group
iterables and optional predicates. To exclude entries before any import,
pass `include`, which receives native metadata only for the selected groups:

```python
excluded_entries = {"experimental"}
restricted = discover_variants(
    groups=(group for group in extension_groups if group != "example.storage"),
    include=lambda entry: entry.name not in excluded_entries,
)
```

A predicate can also inspect `entry.dist.metadata["Name"]` to select providing
distribution packages (these names need not equal Python import package names).
Loading runs plugin Python code. Filtering a loaded snapshot does not undo import
side effects; apply import restrictions in `groups` or `include`. A new discovery
call enumerates metadata again; existing result tuples remain unchanged. No
installation, filesystem/package scanning, hot reload, or automatic subclass
registration is performed by this package.

`select_variants(discovered, *, family, groups=None, extra_variants=())`:

- Filters published records by group before merging explicit types. `groups=None`
  includes all discovered groups; an empty iterable includes none.
- Always considers `extra_variants` regardless of group selection. They have no
  invented entry point or group and belong only to this explicit composition.
- Filters both sources by the requested model ancestry, then deduplicates by class
  identity. First occurrence order is preserved but does not imply priority.
  Unrelated families are filtered, not treated as invalid plugins.
- Supports both top-level and intermediate families. A published `Query` matches
  both `Message[Any]` and `Request[Any]`; publishing only `Request` does not find
  unpublished descendants.
- Supports `DiscriminatedModel` to select all models, a named model class to select
  that class and its descendants, and a closed specialization such as
  `Request[Literal["query"]]` to select that specialization and its descendants.
  Closed specializations preserve their actual class identity and use native
  `issubclass` relationships, not structural field comparisons. Only open views
  resolve to their generic origin; bound tags are not erased.
  Runtime ancestry matching does not claim static assignability to invariant
  `Request[str]`; use `Request[Any]` for an open
  consumer view.
- Returns `tuple[type[DiscriminatedModel], ...]`. The family passed to
  `build_adapter` supplies the static adapter result type; discovering runtime
  plugins cannot create a statically known union.

Selection does not require an open discriminator. Adapter construction still
does: narrow the variants first, then bind them to an open family root:

```python
query_variants = select_variants(
    discovered,
    family=Request[Literal["query"]],
    extra_variants=[Query, LocalQuery],
)
assert LocalQuery not in query_variants
query_adapter = build_adapter(Message[Any], variants=query_variants)
assert type(query_adapter.validate_json(query_adapter.dump_json(query))) is Query
```

Selections make no metadata queries or imports and do not modify their inputs.
They traverse in-memory records; no additional catalog, scope class, or indexing
system is needed. A selection with no matches returns an empty tuple. Supplying
that tuple to an adapter still triggers its existing "No variants" error.

Entry loading failures are re-raised unchanged, with distribution, group, name,
and target information attached using Python's `Exception.add_note()`. Exception
identity, type, message, existing notes, and cause are preserved; callers can
still catch `ModuleNotFoundError`, `ImportError`, or other original exceptions.
Source context appears in traceback notes (`error.__notes__`), not in `str(error)`.
An export that is not a `DiscriminatedModel` class raises `TypeError` with the same source
context. Predicate failures propagate unchanged. Discovery is fail-fast and
does not return partial results or roll back imports that already completed.

Concrete tagged-leaf checks and duplicate tags remain the responsibility of
`bind_variants` and `build_adapter`. An exported open family can be inspected but
cannot be used as a concrete variant. Conflicting models may be used in separate
scopes, but combining them in the same family branch raises an error; neither
group order nor `extra_variants` overrides a discovered implementation. There is
no second tag registry or change to automatic tag defaults.

### One discovery snapshot, independently selected scopes

```python
from typebind import discover, select_implementations

publications = discover(groups={"example.models", "example.behaviors"})
models = select_variants(publications, family=Message[Any], extra_variants=(Query,))
implementations = select_implementations(
    publications,
    groups={"example.behaviors"},
    extra_implementations=(QueryObject,),
)
scoped_runtime = assemble(
    variants=models,
    implementations=implementations,
    required_interfaces=(Describable,),
)
```

`discover(*, groups, include=None)` enumerates native entry points once and
returns a tuple of `DiscoveredVariant | DiscoveredImplementation`. Records retain
their original entry point and distribution. A group may contain both kinds and
multiple families. As with `discover_variants`, groups and predicates filter
before import, a string group argument is rejected, and load failures retain
the original exception with an added source note. The model-only discovery API
remains available without any change to its accepted export kinds.

`select_variants` accepts either snapshot form and ignores behavior records.
`select_implementations` selects behavior records by source group, then merges
`extra_implementations` and deduplicates class identities. The same `groups=None`,
empty groups, iterable consumption and extras semantics apply to both selectors.
Behavior selection does not inspect generic bases or family annotations; the
selected model variants determine the target scope at assembly.
