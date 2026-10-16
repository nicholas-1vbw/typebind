# Model families and serialization

[Back to README](../README.md). See the [model example](../examples/README.md#models-and-manual-assembly)
for a runnable introduction. The snippets below build on each other in order,
except for the standalone recursive-family modules.

## Choosing export semantics

Choose the base at the family root, before declaring its concrete variants:

| Base | Automatic tag defaults | Omitted tags in `exclude_unset=True` |
| --- | --- | --- |
| `DiscriminatedModel` | Native validated defaults | Omitted, as with ordinary Pydantic defaults |
| `IdentityDiscriminatedModel` | The same native defaults | Retained by marking bound identity fields as set |

`IdentityDiscriminatedModel` is a subclass of `DiscriminatedModel`. The longer name
makes the extra identity-marking contract explicit. Both participate in the same
family selection, unions, discovery and behavior assembly. This is a declaration
choice, not a per-instance flag: the basic class has no marking validator or
constructor policy branch. Both bases use the same derived bound-field names to
protect `model_copy(update=...)`; only the identity subclass registers an
after-validator through Pydantic's public API and also uses those names to mark fields as set. Field definitions
remain the single source of truth for both policies.

Ordinary JSON dumps retain tags for both bases. Choose the identity base when
exports using `exclude_unset=True` must remain routable. Changing an existing
family from the identity base to the basic base changes that export contract.
The following guide uses the identity base; the runnable model example
demonstrates both policies.

## Model declarations

Family authors declare one type parameter and one `tag_field()` per level.
The field itself identifies the discriminator; no class keyword repeats its name:

```python
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from typebind import IdentityDiscriminatedModel, bind_variants, build_adapter, tag_field


class Message[Kind: str](IdentityDiscriminatedModel):
    kind: Kind = tag_field()
    identifier: str


class Request[Method: str](Message[Literal["request"]]):
    method: Method = tag_field()
    sequence: int
```

Subtype authors only select a literal and add their fields:

```python
class Query(Request[Literal["query"]]):
    expression: str


class Write(Request[Literal["write"]]):
    value: int


query = Query(identifier="q", sequence=1, expression="hello")
# Static types: query.kind is Literal["request"], query.method is Literal["query"].
```

## Decorator declarations

The explicit generic form above remains available and provides static tag types.
The alternative decorator form generates the discriminator fields at class
creation time. Additional fields, methods, validators and model configuration
belong in the ordinary class body:

```python
from typebind import model


@model(tag="kind")
class DecoratedMessage(IdentityDiscriminatedModel):
    identifier: str


@model(tag="method", variant="request")
class DecoratedRequest(DecoratedMessage):
    sequence: int = 0


@model(variant="query")
class DecoratedQuery(DecoratedRequest):
    expression: str


@model(variant="write")
class DecoratedWrite(DecoratedRequest):
    value: int


decorated = DecoratedQuery(identifier="q", expression="hello")
decorated_binding = bind_variants([DecoratedQuery, DecoratedWrite])


class DecoratedEnvelope(BaseModel):
    requests: list[Annotated[DecoratedRequest, decorated_binding]]


decorated_envelope = DecoratedEnvelope(requests=[decorated])
restored_decorated = DecoratedEnvelope.model_validate_json(
    decorated_envelope.model_dump_json(exclude_unset=True)
)
assert type(restored_decorated.requests[0]) is DecoratedQuery
assert restored_decorated == decorated_envelope
```

`@model(tag="method", variant="request")` binds the inherited open discriminator
(`kind`) to `Literal["request"]` and introduces the next open string field
(`method`). The arguments are keyword-only: `tag` names a field and `variant`
supplies a value for the inherited field, never for the new field. At least one
is required; `@model()` is an error. A root family uses only `tag=`, a leaf uses
only `variant=`, and an intermediate family supplies both in one call.
It cannot bind multiple levels or bypass intermediate fields. A root without an
open parent discriminator rejects `variant=`; existing field/attribute names
cannot be reused by `@model`. Field names must be non-empty strings without a
leading underscore. Python keywords and non-identifier names such as `"class"`
and `"request-type"` are allowed; Pydantic owns the remaining field constraints.

Decorators preserve the static input class type. Pyright checks inherited and
local payload fields, constructor arguments and methods, including wrong/missing
values. It does **not** synthesize `kind`/`method` attributes, tag constructor
arguments, generic parameters or a tag-narrowable union. Accessing an injected
tag attribute is a static error; use model dumps for runtime data, or choose the
explicit generic declaration when application code needs typed tag access.
`@model` does not widen the entire model to `Any`. No `.pyi` files
are generated and no `create_family` API is exposed.

The static limitation applies to constructor keywords as well as attribute
access. For example, this call is accepted and validated at runtime, but strict
Pyright reports the injected `kind` and `method` keywords:

```python
explicit_decorated = DecoratedQuery(
    identifier="q", expression="hello", kind="request", method="query"
)
assert explicit_decorated == decorated
```

Omit tags when constructing a decorated class in statically checked code. Incoming
records can contain tags: use the normal `model_validate` / `model_validate_json`
APIs. Incorrect tag values are rejected, whether supplied to a constructor or
through validation. Explicit generic declarations retain statically typed tag
attributes and constructor arguments.

Internally, `@model` uses Pydantic's `create_model(__base__=original)` to
return an extended subclass. The original class remains in its MRO, preserving
payload validators, methods and zero-argument `super()`. When both arguments are
supplied, it builds both fields in a single extension. Class names, modules and docstrings are
preserved, but a decorator does not preserve the undecorated class object's
identity. Other decorators that register classes should run **after** typebind's
final decorator (appear above it). Each decoration adds one class; MRO length grows
linearly with the number of decorated declarations. `__subclasses__()` can expose
both original and decorated classes. Originals may be untagged or still have an
open discriminator, so raw subclass enumeration is not a variant catalog. Entry
points should export the final decorated class.

Class and schema construction have a declaration-time cost, usually paid during
module import. Classes declared inside functions incur that cost whenever the
function executes the declaration. This is separate from instance construction,
serialization and behavior dispatch; no fixed percentage overhead is promised.

For recursion within the same concrete type, use `Self`, for example
`children: list[Self]`. If `A` and `B` are sibling variants, an `A` with that
annotation cannot accept a `B` child. For mixed-variant children, bind the family
as shown in [recursive family fields](#recursive-family-fields).
A class-name self-reference resolved during the original class body points to
that original class, not its decorated replacement; nested values then validate
as the undecorated class. This is not a supported recursive declaration form for
decorators. Explicit generics and ordinary Pydantic unions remain available when
that distinction matters. Custom class-construction side effects may run again
for the native generated subclass; typebind adds no custom metaclass.

The generated fields are the single source of discriminator names and Literal
values. Field metadata only marks the declaration style, allowing the original
subclass to exist before decoration. It contains no copied tag names/values.
An accidentally undecorated subclass retains an open discriminator and is
rejected as a leaf at adapter/behavior assembly. Explicit generic declarations
keep their earlier declaration-time diagnostics. Introducing another level
requires binding the inherited discriminator first.

Both forms share native default compilation, duplicate checks, serialization
and export policy. Decorated types work with the same `build_adapter`,
`bind_variants`, entry points, `select_variants`, `Implementation[Model]` and
`assemble` APIs. Publish the final decorated class via an entry point. Reflection
and class generation happen during declaration/assembly, never construction or
`adapt`. Use `DiscriminatedModel` instead of the identity base above for native
unset behavior without an identity-marking callback.

Forms can coexist: a decorated family can extend a bound explicit parent such as
`Message[Literal["request"]]`, and an explicit generic family can extend a bound
decorated leaf. An unbound explicit generic must first be specialized with native
`[Literal[...]]`; decorators do not replace Pydantic's generic specialization.

## Specialized model aliases

When a subtype only selects a tag and adds no fields or behavior, an ordinary
assignment can name the native specialized model directly:

```python
Ping = Message[Literal["ping"]]

ping = Ping(identifier="p")
assert ping.kind == "ping"
assert Ping.model_validate_json(ping.model_dump_json()) == ping
```

`Ping` is a callable model class alias, not a separate subclass. Its tag remains
`Literal["ping"]` under strict Pyright, and omitted tags still get their automatic
values. Use a class declaration when you need additional fields, validators, a
class docstring, or a distinct class identity. Use ordinary assignment here rather
than `type Ping = ...`; the named type aliases below are intended as annotations.

## Consumer types

Generic consumers can preserve the tag parameter without widening it to `str`:

```python
def get_method[T: str](request: Request[T]) -> T:
    return request.method


method = get_method(query)  # Literal["query"]
```

For a known set of subtypes, use an explicit union:

```python
class Document(BaseModel):
    primary: Annotated[Query | Write, Field(discriminator="method")]
    requests: list[Annotated[Query | Write, Field(discriminator="method")]]


document = Document(primary=query, requests=[query])
adapter = TypeAdapter(Document)
wire = adapter.dump_json(document)
restored = adapter.validate_json(wire)
assert type(restored.primary) is Query
assert restored == document
```

For an open family whose concrete subtypes are selected at runtime, explicitly
leave its tag parameter unspecified with `Any` and bind the variants explicitly:

```python
from typing import Any


VARIANTS = bind_variants([Query, Write])


class OpenDocument(BaseModel):
    primary: Annotated[Message[Any], VARIANTS]
    requests: list[Annotated[Request[Any], VARIANTS]]


adapter = TypeAdapter(OpenDocument)
document = OpenDocument(primary=query, requests=[query])
restored = adapter.validate_json(adapter.dump_json(document))
assert type(restored.primary) is Query
assert restored == document
```

Repeated field bindings can use native named type aliases. Reuse the same binding
so the variant set is declared once:

```python
type MessageValue = Annotated[Message[Any], VARIANTS]
type RequestValue = Annotated[Request[Any], VARIANTS]


class AliasedDocument(BaseModel):
    primary: MessageValue
    requests: list[RequestValue]


aliased = AliasedDocument(primary=query, requests=[query])
restored_alias = AliasedDocument.model_validate_json(aliased.model_dump_json())
assert restored_alias == aliased
assert type(restored_alias.primary) is Query
assert type(restored_alias.requests[0]) is Query
```

These aliases preserve the static common-field types and the explicit runtime
variant binding. They work with native model methods and `TypeAdapter` without
adding a registry or another class-generation mechanism.

`Any` here applies to the discriminator parameter, not the whole model: common
fields such as `identifier: str` and `sequence: int` remain checked. Concrete
subtypes still have exact `Literal` tags. This open-family boundary deliberately
gives up a closed static tag set; runtime variant selection cannot generate a
union visible to static checkers. A root family can likewise use
`build_adapter(Message[Any], variants=[Query, Write])`.

There is no default type argument or required covariance. The tested native
fields infer invariant parameters in strict Pyright, and `Query` is not accepted
where `Request[str]` is required. Bare generic instance annotations also require
an explicit parameter under strict checking. Do not use `Request[str]` to mean
"any Request".

## Declaration rules and ownership

- Pydantic owns `__class_getitem__`, generic field substitution, and specialization
  caching for explicit declarations. Decorators use native `create_model` as
  described above. This package does not override subscription, mutate `model_fields`, or rebuild models to supply defaults. There is no custom
  `__init_subclass__`; the Pydantic `__pydantic_init_subclass__` hook inspects the
  completed fields and validates the declaration. It stores no per-class tag
  values, discriminator names, or inheritance paths.
- Each explicit generic family declares one `str`-bound type parameter as its outgoing discriminator
  field. Independent roots share only the mechanism. A branch can introduce a new
  type parameter and discriminator for the next level. More than one new tag
  field at the same level is rejected; inherited fixed tag fields do not count.
  Variance is not checked by
  this package; the native type checker determines assignability.
- `Parent[Literal["tag"]]` selects a concrete tag. Tags must be singleton, non-empty
  string literals; cardinality and empty-string restrictions are checked at runtime.
  Generic consumer annotations such as `Request[T]` leave the family open.
- Use `kind: Kind = tag_field()` in assignment form. The helper returns a native
  frozen `Field` with a private default factory. Its factory identity identifies
  tag fields after Pydantic merges and specializes fields; no separate registry
  or custom metadata type is needed. The assigned default tells Pyright that
  constructors can omit the tag, including on an open family: strict Pyright
  accepts `Message[Any](identifier="m")`, but runtime validation rejects its
  missing `kind`. During schema generation, typebind reads the
  specialized field annotation and replaces that factory's schema with a native
  default and `validate_default=True`. Open tags become required fields. Explicit
  values and automatic defaults both pass through the field's validators.
- `tag_field()` returns `Any`, like Pydantic's `Field`, for assignment to a generic
  field; the annotation still determines consumer types. The declaration factory
  is a marker, not an instance-time default generator. `model_fields` continues to
  expose that declaration; it is not authoritative for tag requiredness or runtime
  defaults. For an open tag, `FieldInfo.is_required()` returns `False` even though
  validation requires input. For a bound tag, `FieldInfo.default` remains
  `PydanticUndefined`; `get_default()` does not supply the compiled literal, and
  `get_default(call_default_factory=True)` raises instead of returning the tag.
  Use `model_json_schema()` to inspect compiled tag requiredness/defaults, and
  normal model validation to apply them. Both bases derive bound discriminator names at class declaration
  and refresh them during schema generation/rebuild, keeping one immutable tuple
  derived from the fields. This protects copy updates, including deferred models.
  On `IdentityDiscriminatedModel`, an after-validator registered through the public
  `model_validator(mode="after")` API also uses this tuple to update the instance's public `model_fields_set`
  before ordinary subclass after-validators. It performs no field scan,
  annotation resolution, or input copying. Pydantic may run after-validators again
  when nesting existing instances; the set update is idempotent.
  Its method name, `_mark_bound_tags`, is internal. Defining a subclass validator
  with the same name overrides it under Pydantic's native rules and can remove
  identity marking; use a distinct name for application validators.
- For the identity base, validator composition follows Pydantic's native MRO order.
  An after-validator
  inherited from another mixin can run before the base marking validator (for
  example, `class Leaf(Family[Literal["leaf"]], ObserverMixin)`). Such a validator
  must not assume omitted tags are already marked as set or retained by
  `exclude_unset` at that point. Ordinary subclass after-validators run after the
  base marker, and completed validated instances retain bound tags. Typebind does
  not reorder native validators to enforce earlier marking across arbitrary mixins.
- With `IdentityDiscriminatedModel`, bound discriminators represent type identity,
  so they enter `model_fields_set`
  even when omitted by the caller. Ordinary dumps and `exclude_unset=True` retain
  them; unrelated omitted payload defaults stay unset. Explicitly supplied tags
  follow the same path. Open tags still require input.
- Other export options retain native semantics: `exclude_defaults=True` omits
  values equal to the default, even when marked as set. Explicit `include` and
  `exclude` selections can also remove tags and take precedence over their set
  status. Do not use options that remove routing tags for family interchange.
- Discriminator fields are frozen at runtime to prevent retagging. Other fields
  remain mutable. Field-level freezing is not treated as a portable static
  read-only guarantee and is not used to justify covariance.
- An unbound family has no automatic tag value. An explicit discriminator value
  is required when validating it directly. Family dispatch through an adapter
  requires tags in incoming data even though concrete constructors can omit them.
- All ancestors' tags are preserved. Selecting multiple tags at once cannot skip
  intermediate model fields or validators. Fixed discriminator fields cannot be
  changed by a subclass. Discriminator aliases and exclusion are unsupported.
- Supply tagged leaves explicitly to `build_adapter` or `bind_variants`. Native
  specializations such as `Request[Literal["query"]]` are also valid leaves. Two different classes claiming
  the same tag under the same parent fail during adapter construction, including
  intermediate branch conflicts. Tags may repeat in different families/branches.
  Repeating the same class in the input is harmless.

## Declaration diagnostics

Package declaration errors identify the module-qualified model and the affected
field, describe the invalid declaration, and suggest a correction. For example,
a marked field without a `str`-bound type parameter reports the field annotation
and type parameters together with the expected declaration form. Multiple new
tag fields are listed by name; unsupported field settings such as `exclude_if`
are identified explicitly. Inherited tag changes report both the fixed and
attempted values.

Duplicate tags are checked when binding variants or building an adapter. Errors
identify the owning family and discriminator, the conflicting value, and both
module-qualified branch classes. For example:

```text
Duplicate tag 'query' for protocol.Request.method: extension_one.Query and extension_two.Query claim the same branch. Use distinct Literal tags or include only one of these branches in variants.
```

This also identifies intermediate branch conflicts. Repeating the same class is
still harmless. Native Python and Pydantic errors retain their own diagnostics.

## Adapter boundary

`build_adapter` accepts an open family root. It assembles nested
`Annotated[Union[...], Field(discriminator=...)]` expressions from the supplied
native inheritance and resolved leaf fields, then passes the resulting type to
a normal `TypeAdapter`. The MRO identifies family boundaries; `model_fields`
provides discriminator names and Literal values. Duplicate checks and dispatch
use the same local table. Automatic defaults are derived from the same fields
when Pydantic generates the model's schema. The public schema hook replaces only
marked defaults along the model's validator wrappers; payload schemas and native
references are preserved. The identity subclass adds an after-validator that
marks bound identity fields as set through the public `model_fields_set` property. The derived field-name tuple
is initialized during declaration and refreshed during schema generation; it
contains no tag values and is shared with the copy-update guard. There is no
parallel tag registry, cached path, or annotation-result cache to synchronize.

For nested fields, `bind_variants` captures the supplied leaves and returns
Pydantic's `GetPydanticSchema` metadata. The metadata asks Pydantic to generate the
same nested union at the explicitly annotated field. Containers then use normal
constructors, `model_validate`, `model_dump_json`, and `TypeAdapter`. Existing
unbound containers are not rewritten; a plain `Message[Any]` annotation keeps
its native Pydantic meaning and does not dispatch to concrete leaves.
It also serializes according to the declared family schema: a `Query` stored in
a bare `Message[Any]` field can lose `method`, `sequence`, and `expression` on
export. Use an explicit union or `bind_variants` when those subtype fields must
survive a round trip.

Lists, dictionaries, optional fields, and independent family bindings are tested.
Known recursive trees can declare recursive native discriminated unions directly.
Payload validators remain inherited. Native discriminated unions reject
`before`, `plain`, and `wrap` field validators on discriminator fields; normalization
that changes a routing tag is not supported by this binding mechanism.

Bindings and adapters capture independent variant snapshots. Their local dispatch
tables and compiled schemas are derived artifacts, not separate declarations. Build a new binding or
adapter when the variant set changes. No already compiled validators, serializers,
or adapter attributes are patched. Mutating model fields after declaration is outside this package's
contract; this does not provide live schema synchronization. Native
`TypeAdapter.rebuild(force=True)`, JSON Schema generation,
validation, and serialization operate on the actual union annotation.

Automatic tags support constructors, JSON, mappings, and attribute-object inputs
(`from_attributes=True`) when validating a concrete model. An open family still
requires its tag, and a discriminated union must read routing tags before selecting
any leaf defaults. User model validators retain Pydantic's native ordering: a
before-model validator sees the original input, before defaults are applied.
Unchecked `model_construct()` does not execute the compiled schema and must supply
tags explicitly; the declaration factory raises if called rather than returning
an unresolved sentinel. It is not a persistence/deserialization API.

## Recursive family fields

To accept sibling variants in a recursive field, bind the family rather than
`Self`. Both declaration styles below use the same binding and rebuild sequence.
Each example is a standalone module. Put the future import at the top
so the binding name can be defined after both leaves exist; then rebuild the
leaves before constructing instances or adapters.

### Explicit generic declarations

Bind `Node[Any]` for the children so each child can select its own tag independently
of its parent's tag. Define the open-family alias after the generic class so
specialization waits until the family's fields have been collected:

```python
from __future__ import annotations

from typing import Annotated, Any, Literal

from typebind import IdentityDiscriminatedModel, bind_variants, build_adapter, tag_field


class Node[Kind: str](IdentityDiscriminatedModel):
    kind: Kind = tag_field()
    children: list[Annotated[NodeFamily, NODE_VARIANTS]] = []


NodeFamily = Node[Any]


class A(Node[Literal["a"]]):
    value: int


class B(Node[Literal["b"]]):
    text: str


NODE_VARIANTS = bind_variants([A, B])
A.model_rebuild()
B.model_rebuild()

tree = A(value=1, children=[B(text="child")])
adapter = build_adapter(Node[Any], variants=[A, B])
restored = adapter.validate_json(adapter.dump_json(tree))
assert isinstance(restored, A)
assert isinstance(restored.children[0], B)
assert restored == tree
```

Each child's static type remains `Node[Any]`. The parent's `Kind` does not constrain
the child's tag; narrow the child before accessing a leaf-specific field.

### Decorator declarations

For decorated families, bind `Node` directly because this form has no explicit
generic tag parameter:

```python
from __future__ import annotations

from typing import Annotated

from typebind import IdentityDiscriminatedModel, bind_variants, build_adapter, model


@model(tag="kind")
class Node(IdentityDiscriminatedModel):
    children: list[Annotated[Node, NODE_VARIANTS]] = []


@model(variant="a")
class A(Node):
    value: int


@model(variant="b")
class B(Node):
    text: str


NODE_VARIANTS = bind_variants([A, B])
A.model_rebuild()
B.model_rebuild()

tree = A(value=1, children=[B(text="child")])
adapter = build_adapter(Node, variants=[A, B])
restored = adapter.validate_json(adapter.dump_json(tree))
assert isinstance(restored, A)
assert isinstance(restored.children[0], B)
assert restored == tree
```

The compiled children field dispatches through the explicit `[A, B]` snapshot.
Each child's static type remains `Node`; narrow it before accessing a leaf-specific field.

## Identity and copying

Validated construction accepts omitted tags or explicit values matching their
Literal declarations. Wrong tag values raise a validation error. Attribute
assignment to a discriminator is rejected by Pydantic's frozen field behavior,
even if the assigned value is unchanged.

Both bases also reject `model_copy(update=...)` when the update includes any
**bound** discriminator, including a same-value update and `deep=True`. The check
runs before copying and raises `ValueError` identifying the model and field.
It checks update keys against the declaration-derived name tuple; it does not
scan fields, resolve annotations or compare tag values on the copy path.

On Python 3.13+, `copy.replace(instance, **changes)` delegates through Pydantic's
`__replace__` to `model_copy(update=changes)`, so the same bound-tag protection
applies. Payload-only replacements remain supported; no separate replacement hook
is needed. Python 3.12 does not provide `copy.replace()`.

Ordinary copies and payload changes remain available:

```python
copied_query = query.model_copy(update={"expression": "updated"}, deep=True)
assert copied_query.expression == "updated"
assert copied_query.kind == query.kind
assert copied_query.method == query.method
```

Other updates preserve native Pydantic semantics: they are **not validated** and
are not applied recursively. Open discriminator fields have no bound identity
and retain native copy-update behavior. Nested payload replacement can likewise
introduce unvalidated data; use normal validation when accepting external input.
The guard adds no callbacks to constructors, `model_dump*`, `model_validate*` or
`TypeAdapter` serialization/validation. Only explicit copy operations check updates.

This is an API contract, not tamper-proof Python immutability. Direct internal
mutation, explicitly invoking an ancestor implementation, and unchecked
`model_construct()` are not validated data-entry APIs. The deprecated Pydantic
`copy()` API is not extended; use `model_copy()`.

## JSON serialization contract

Persistence and message interchange use Pydantic JSON round trips. For a family,
reuse the assembled `TypeAdapter` for `dump_json()` and `validate_json()`. For a
concrete model or envelope with explicitly bound family fields, use
`model_dump_json()` and `model_validate_json()`. The reader must have the required
variant definitions and binding; unknown discriminator values are rejected.
Ordinary model and adapter dumps retain all routing tags. Keep those tags and
required payload fields in serialized records. For `IdentityDiscriminatedModel`,
`exclude_unset=True` retains all bound tags while omitting unrelated unset defaults, including in nested family
fields. For `DiscriminatedModel`, omitted default tags are excluded, so use an
ordinary dump for family interchange. A known concrete leaf can restore its own
omitted tag defaults, but a family union needs those tags to choose the leaf
first. `exclude_defaults=True` and explicit field
exclusions can still remove tags, even when combined with `exclude_unset=True`; such outputs cannot be relied
on for family round trips. Native union validation
rejects missing tags with `union_tag_not_found` before selecting a concrete model.

`model_dump()` defaults to Python mode and can contain objects such as UUIDs or
datetimes. If a storage or messaging SDK performs JSON encoding, pass
`model_dump(mode="json")` or `adapter.dump_python(mode="json")` instead.
Serialization does not replace input validation: use validated instances and
`warnings="error"` to surface serializer warnings. Custom serializers and lossy
field exclusions can change round-trip behavior. Schema-version compatibility
and migrations belong to the consumer; JSON does not make them automatic.

Pickle is not suitable for this durable/interchange contract and is not a
compatibility or acceptance requirement. It relies on Python object/import
identity rather than the explicit discriminator schema; generic or local class
importability is not guaranteed by typebind. No pickle-specific hooks are added.
Persist model data only; rebuild plugin selection, adapters, and behavior objects
in each receiving process. Unchecked `model_construct()` is not deserialization
and does not supply the validated automatic-tag guarantees.

## Current boundaries

Strict Pyright is verified; PyCharm and Mypy are not.

The declaration hook reads Pydantic's generic metadata to identify the originating
family. Default generation uses the public core-schema hook and native
`with_default_schema`; union generation uses public Pydantic APIs. No private
compiler options or custom generic-class creation are used. The supported Pydantic
range is specified in `pyproject.toml`; Pydantic selects its matching pydantic-core
version. The minimum is 2.11.2: earlier 2.11 releases fail nested discriminator
assembly. On versions without `exclude_if`, that field option has no exclusion
policy to check. When changing dependencies, verify these compatibility boundaries:

- Native `create_model` preserves decorated payload fields, validators, `Self`,
  schema rebuilds and inheritance; strict Pyright preserves business-field types.
- Generic metadata and specialized fields still identify families and resolve
  Literal tags, including type aliases.
- Core-schema generation still replaces the tag factory with native defaults and
  preserves validator wrappers, nested references, and model rebuild behavior.
- For the identity subclass, the inherited `model_validator(mode="after")` still marks bound tags through
  the public `model_fields_set` property before ordinary subclass after-validators
  run; mixin validators retain native MRO ordering. Nested validation can run
  this validator again on an existing instance;
  the set update must remain idempotent. No model-fields result tuple is inspected.
- For both bases, declaration and schema generation, including `model_rebuild()`,
  derive bound field names from `model_fields`. `model_copy(update=...)` rejects
  bound tag keys even before a deferred schema is built; payload updates retain
  native shallow/deep copy semantics. Field definitions remain authoritative;
  instances must not scan fields or resolve tags.
- Identity models retain omitted and explicit tags in `exclude_unset=True`,
  including multilevel
  models and nested JSON round trips. Ordinary payload defaults remain unset;
  `exclude_defaults` and explicit inclusion/exclusion retain native semantics.
  Basic models retain native unset semantics and compile without a marking callback.

Run the existing model regression tests and the full `poe check` suite for the
locked candidate. The separate minimum-dependency check also runs strict typing,
library tests, examples and isolated wheel verification with its resolved runtime
constraints; see [Contributing](../CONTRIBUTING.md#set-up-and-run-checks).

Each explicit family has one discriminator type parameter; decorated families
have a runtime string field instead. Additional payload type parameters,
discriminator
aliases, and multiple DiscriminatedModel parents remain outside this package's
contract. These are current implementation limitations, not general requirements of Pydantic.
Custom constructors and arbitrary custom schema transformations are outside the
compatibility guarantee. An override of `__get_pydantic_core_schema__` must preserve
this base class's hook by calling `super()` to retain automatic tags. Regression
tests cover delegation and the diagnostic when an override bypasses that hook;
they do not establish compatibility with every custom schema transformation.
Dynamic type-expression assembly and the public field helper use localized `Any`;
open consumers explicitly opt into `Family[Any]`. Concrete tag parameters and
payload fields retain their declared types.
