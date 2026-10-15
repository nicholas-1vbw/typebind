# Model families and serialization

[Back to README](../README.md). Run `uv run --locked poe example-models` for a
self-contained introduction. The snippets below build on each other in order.

## Model declarations

Family authors declare one type parameter and one `tag_field()` per level.
The field itself identifies the discriminator; no class keyword repeats its name:

```python
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from typebind import DiscriminatedModel, bind_variants, build_adapter, tag_field


class Message[Kind: str](DiscriminatedModel):
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
  caching. This package does not override subscription, call `create_model`,
  mutate `model_fields`, or rebuild models to supply defaults. There is no custom
  `__init_subclass__`; the Pydantic `__pydantic_init_subclass__` hook inspects the
  completed fields and validates the declaration. It stores no per-class tag
  values, discriminator names, or inheritance paths.
- Each family declares one `str`-bound type parameter as its outgoing discriminator
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
  constructors can omit the tag. During schema generation, typebind reads the
  specialized field annotation and replaces that factory's schema with a native
  default and `validate_default=True`. Open tags become required fields. Explicit
  values and automatic defaults both pass through the field's validators.
- `tag_field()` returns `Any`, like Pydantic's `Field`, for assignment to a generic
  field; the annotation still determines consumer types. The declaration factory
  is a marker, not an instance-time default generator. `model_fields` continues to
  expose that declaration; compiled validation and JSON Schema expose the derived
  literal default. Schema generation also captures the bound discriminator names
  in an immutable class-level tuple derived from the fields. A base
  `model_validator(mode="after")` updates the instance's public `model_fields_set`
  before ordinary subclass after-validators. It performs no field scan,
  annotation resolution, or input copying. Pydantic may run after-validators again
  when nesting existing instances; the set update is idempotent.
- Validator composition follows Pydantic's native MRO order. An after-validator
  inherited from another mixin can run before the base marking validator (for
  example, `class Leaf(Family[Literal["leaf"]], ObserverMixin)`). Such a validator
  must not assume omitted tags are already marked as set or retained by
  `exclude_unset` at that point. Ordinary subclass after-validators run after the
  base marker, and completed validated instances retain bound tags. Typebind does
  not reorder native validators to enforce earlier marking across arbitrary mixins.
- Bound discriminators represent type identity, so they enter `model_fields_set`
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
references are preserved. A base after-validator marks bound identity fields as
set through the public `model_fields_set` property. The derived field-name tuple
is refreshed during schema generation; it contains no tag values. There is no
parallel tag registry, cached path, or annotation-result cache to synchronize.

For nested fields, `bind_variants` captures the supplied leaves and returns
Pydantic's `GetPydanticSchema` metadata. The metadata asks Pydantic to generate the
same nested union at the explicitly annotated field. Containers then use normal
constructors, `model_validate`, `model_dump_json`, and `TypeAdapter`. Existing
unbound containers are not rewritten; a plain `Message[Any]` annotation keeps
its native Pydantic meaning and does not dispatch to concrete leaves.

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

## JSON serialization contract

Persistence and message interchange use Pydantic JSON round trips. For a family,
reuse the assembled `TypeAdapter` for `dump_json()` and `validate_json()`. For a
concrete model or envelope with explicitly bound family fields, use
`model_dump_json()` and `model_validate_json()`. The reader must have the required
variant definitions and binding; unknown discriminator values are rejected.
Ordinary model and adapter dumps retain all routing tags. Keep those tags and
required payload fields in serialized records. `exclude_unset=True` retains all
bound tags while omitting unrelated unset defaults, including in nested family
fields. `exclude_defaults=True` and explicit field exclusions can still remove
tags, even when combined with `exclude_unset=True`; such outputs cannot be relied
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

The declaration hook reads Pydantic's generic metadata to identify the originating
family. Default generation uses the public core-schema hook and native
`with_default_schema`; union generation uses public Pydantic APIs. No private
compiler options or custom generic-class creation are used. The supported Pydantic minor series is specified in
`pyproject.toml`. Before widening that range or upgrading the resolved Pydantic /
pydantic-core versions, verify these compatibility boundaries:

- Generic metadata and specialized fields still identify families and resolve
  Literal tags, including type aliases.
- Core-schema generation still replaces the tag factory with native defaults and
  preserves validator wrappers, nested references, and model rebuild behavior.
- The inherited `model_validator(mode="after")` still marks bound tags through
  the public `model_fields_set` property before ordinary subclass after-validators
  run; mixin validators retain native MRO ordering. Nested validation can run
  this validator again on an existing instance;
  the set update must remain idempotent. No model-fields result tuple is inspected.
- Schema generation, including `model_rebuild()`, refreshes the derived tuple of
  bound field names from `model_fields`. Field definitions remain authoritative;
  instances must not scan fields or resolve tags.
- Omitted and explicit tags survive `exclude_unset=True`, including multilevel
  models and nested JSON round trips. Ordinary payload defaults remain unset;
  `exclude_defaults` and explicit inclusion/exclusion retain native semantics.

Run the existing model regression tests and the full `poe check` suite against
candidate dependency versions. The version upper bound limits the supported
range; it does not replace these compatibility checks.

Each family has one discriminator type parameter. Additional payload type
parameters, refinement of an open family without selecting its tag, discriminator
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
