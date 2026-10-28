"""Generic/decorator declarations and explicit Pydantic discriminated unions."""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextvars import ContextVar
from dataclasses import dataclass
from inspect import isabstract
from itertools import count
from typing import (
    Annotated,
    Any,
    ClassVar,
    ForwardRef,
    Literal,
    Self,
    TypeAliasType,
    TypeVar,
    cast,
    get_args,
    get_origin,
)
from weakref import WeakKeyDictionary

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    GetPydanticSchema,
    TypeAdapter,
    create_model,
    model_validator,
)
from pydantic.fields import FieldInfo
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, PydanticCustomError, core_schema

# A declaration-style marker, stored only on generated fields. Tag names and
# values are still read exclusively from the finalized Pydantic model_fields.
_DECORATED_TAG = object()


@dataclass(frozen=True)
class _TagValidator:
    validate: Callable[[str], None]


def _tag_validator(field: FieldInfo) -> Callable[[str], None] | None:
    return next((item.validate for item in field.metadata if isinstance(item, _TagValidator)), None)


def _decorated_field(field: FieldInfo) -> bool:
    return any(item is _DECORATED_TAG for item in field.metadata)


def _tag_default() -> object:
    """Identify tag declarations; unchecked construction must supply tags itself."""
    raise ValueError(
        "Automatic tag defaults were not compiled. "
        "When using model_construct(), supply tags explicitly. "
        "If overriding __get_pydantic_core_schema__, preserve the "
        "DiscriminatedModel schema hook by calling super()."
    )


def tag_field(*, validator: Callable[[str], None] | None = None) -> Any:
    """Declare this level's discriminator as a native frozen Pydantic field.

    The private default factory identifies tag fields after native field merging
    and generic substitution. Schema generation replaces it with a validated
    native default for bound tags, or a required field for open tags.
    DiscriminatedModel keeps native unset semantics; IdentityDiscriminatedModel
    additionally marks bound tags as set through Pydantic's public validator API.

    ``validator`` belongs to this family and checks bound variant values during
    declaration and assembly. It must return None or raise ValueError, without
    modifying the value. It is not an instance-time Pydantic field validator.

    Like Pydantic's Field, this helper returns Any for assignment to a typed
    field. The explicit annotation owns the field and constructor argument types;
    the assigned default makes the argument optional to static type checkers.
    """
    if validator is not None and not callable(validator):
        raise TypeError("validator must be a callable accepting a string, or None")
    field = cast(FieldInfo, Field(default_factory=_tag_default, frozen=True))
    if validator is not None:
        field.metadata.append(_TagValidator(validator))
    return field


def _model_name(model: type[BaseModel]) -> str:
    """Identify declarations unambiguously across extension packages."""
    return f"{model.__module__}.{model.__qualname__}"


def _tag_fields(model: type[DiscriminatedModel]) -> dict[str, FieldInfo]:
    """Read marked fields after Pydantic's native merging and specialization."""
    return {
        name: field
        for name, field in model.model_fields.items()
        if field.default_factory is _tag_default
    }


def _is_open_tag(annotation: object) -> bool:
    """Recognize the unbound field types supported by family views and consumers."""
    return annotation is str or annotation is Any or isinstance(annotation, TypeVar)


def _open_discriminator(model: type[DiscriminatedModel]) -> str | None:
    """Read the open discriminator of an already checked model, if any."""
    return next(
        (name for name, field in _tag_fields(model).items() if _is_open_tag(field.annotation)),
        None,
    )


def _bound_names(model: type[DiscriminatedModel]) -> tuple[str, ...]:
    """Compile the identity field names from authoritative resolved fields."""
    return tuple(
        name for name, field in _tag_fields(model).items() if not _is_open_tag(field.annotation)
    )


def _literal_tag(model: type[DiscriminatedModel], name: str) -> str:
    """Resolve the authoritative field through Pydantic at declaration/assembly time."""
    annotation = model.model_fields[name].annotation
    schema = TypeAdapter(cast(Any, annotation)).core_schema
    if schema["type"] == "literal" and len(schema["expected"]) == 1:
        value: object = schema["expected"][0]
        if isinstance(value, str) and value:
            validator = _tag_validator(model.model_fields[name])
            if validator is not None:
                try:
                    result = cast(Callable[[str], object], validator)(value)
                except ValueError as error:
                    raise ValueError(
                        f"{_model_name(model)}.{name}: invalid variant {value!r}: {error}"
                    ) from error
                if result is not None:
                    raise TypeError(
                        f"{_model_name(model)}.{name}: validator for variant {value!r} "
                        "must return None or raise ValueError; "
                        f"got {type(result).__name__}"
                    )
            return value
    raise TypeError(
        f"{_model_name(model)}.{name}: a tag must be a single non-empty string Literal; "
        f"got {annotation!r}. Select a tag with Family[Literal['tag']]."
    )


def _with_tag_defaults(schema: CoreSchema, model: type[DiscriminatedModel]) -> CoreSchema:
    """Compile defaults on this model's fields, preserving Pydantic's validator wrappers.

    Copy only the model spine and marked fields during schema generation. Payload
    schemas and referenced models retain their native definitions. Reference nodes
    already point to schemas built by the referenced model's own hook.
    """
    if schema["type"] == "model-fields":
        inner = schema
        fields = dict(inner["fields"])
        for name, field in _tag_fields(model).items():
            original = fields[name]["schema"]
            # Pydantic can hand the hook an existing schema when embedding a model.
            if original["type"] != "default":
                continue
            default = cast(core_schema.WithDefaultSchema, original)
            if default.get("default_factory") is not _tag_default:
                continue
            resolved = default["schema"]
            if not _is_open_tag(field.annotation):
                resolved = core_schema.with_default_schema(
                    resolved, default=_literal_tag(model, name), validate_default=True
                )
            fields[name] = {**fields[name], "schema": resolved}
        return {**inner, "fields": fields}
    if schema["type"] == "model":
        return {**schema, "schema": _with_tag_defaults(schema["schema"], model)}
    if schema["type"] in ("function-before", "function-after", "function-wrap"):
        wrapper = cast(
            core_schema.BeforeValidatorFunctionSchema
            | core_schema.AfterValidatorFunctionSchema
            | core_schema.WrapValidatorFunctionSchema,
            schema,
        )
        updated = wrapper.copy()
        updated["schema"] = _with_tag_defaults(wrapper["schema"], model)
        return updated
    return schema


class _ScopeMarker:
    """Base of annotation metadata that already decides a family field's binding.

    Explicit bindings derive from it, so annotation walks treat the field as
    declared rather than open.
    """


def _scope_marker(metadata: object) -> _ScopeMarker | None:
    """Recognize a marker directly or through the GetPydanticSchema wrapper."""
    if isinstance(metadata, _ScopeMarker):
        return metadata
    if isinstance(metadata, GetPydanticSchema):
        owner = getattr(metadata.get_pydantic_core_schema, "__self__", None)
        if isinstance(owner, _ScopeMarker):
            return owner
    return None


# Classes replaced by @model, mapped to their replacement. A class-name reference
# resolved inside the original class body points at the original; scopes resolve it
# to the decorated family so it is bound or rejected, never parsed as the original.
_replaced: WeakKeyDictionary[type, type] = WeakKeyDictionary()


def _resolve_replaced(value: object) -> object:
    while isinstance(value, type) and value in _replaced:
        value = _replaced[value]
    return value


def _is_open_family(value: object) -> bool:
    value = _resolve_replaced(value)
    return (
        isinstance(value, type)
        and issubclass(value, DiscriminatedModel)
        and _open_discriminator(value) is not None
    )


def _unalias(annotation: object) -> object:
    """Follow PEP 695 aliases so their value is walked like an inline annotation."""
    while isinstance(annotation, TypeAliasType):
        annotation = annotation.__value__
    return annotation


def _open_families(annotation: object) -> list[type[DiscriminatedModel]]:
    """Open families an annotation declares as automatically bindable positions."""
    annotation = _unalias(annotation)
    if _is_open_family(annotation):
        return [cast(type[DiscriminatedModel], annotation)]
    if get_origin(annotation) is Annotated:
        if any(_scope_marker(item) for item in cast(Any, annotation).__metadata__):
            return []
        return _open_families(get_args(annotation)[0])
    return [family for arg in get_args(annotation) for family in _open_families(arg)]


def _serialize_as_any(node: Any, refs: set[str], families: set[type]) -> Any:
    """Mark family positions like Pydantic's SerializeAsAny, without callbacks."""
    if isinstance(node, list):
        return [_serialize_as_any(item, refs, families) for item in cast(list[Any], node)]
    if isinstance(node, tuple):
        # Labelled union choices are (schema, label) pairs.
        return tuple(_serialize_as_any(item, refs, families) for item in cast(tuple[Any], node))
    if not isinstance(node, dict):
        return node
    item = cast(dict[str, Any], node)
    kind = item.get("type")
    if (kind == "definition-ref" and item.get("schema_ref") in refs) or (
        kind == "model" and item.get("cls") in families
    ):
        return {**item, "serialization": core_schema.simple_ser_schema("any")}
    serialization = item.get("serialization")
    out = _map_schema(
        {key: value for key, value in item.items() if key != "serialization"},
        lambda value: _serialize_as_any(value, refs, families),
    )
    if serialization is not None:
        out["serialization"] = serialization
    return out


def _with_runtime_serialization(
    schema: CoreSchema, model: type[BaseModel], handler: GetCoreSchemaHandler
) -> CoreSchema:
    """Serialize open-family fields with the runtime class, as SerializeAsAny does.

    Only positions declared as open families are marked; explicit bindings and
    concrete declarations keep their native serializers. Applied once per class
    declaration; validation and other fields are unchanged.
    """
    if schema["type"] == "model-fields":
        fields = dict(schema["fields"])
        for name, field in model.model_fields.items():
            if field.default_factory is _tag_default or name not in fields:
                continue
            if any(_scope_marker(item) for item in field.metadata):
                continue
            families = _open_families(field.annotation)
            if not families:
                continue
            refs: set[str] = set()
            for family in families:
                generated = handler.generate_schema(family)
                if generated["type"] == "definition-ref":
                    refs.add(generated["schema_ref"])
            fields[name] = _serialize_as_any(fields[name], refs, set[type](families))
        return {**schema, "fields": fields}
    if schema["type"] == "model":
        return {**schema, "schema": _with_runtime_serialization(schema["schema"], model, handler)}
    if schema["type"] in ("function-before", "function-after", "function-wrap"):
        wrapper = cast(
            core_schema.BeforeValidatorFunctionSchema
            | core_schema.AfterValidatorFunctionSchema
            | core_schema.WrapValidatorFunctionSchema,
            schema,
        )
        updated = wrapper.copy()
        updated["schema"] = _with_runtime_serialization(wrapper["schema"], model, handler)
        return updated
    return schema


class DiscriminatedModel(BaseModel):
    """Declare model families with one discriminator per level.

    A family declares a str-bound type parameter and marks its field with
    ``tag_field()``. Subclasses select a tag through native
    ``Family[Literal["tag"]]`` specialization and may introduce another family.
    Alternatively, @model generates runtime-only tag fields.
    The field declaration is the sole source of its name, type, and tag behavior.

    These hooks validate declarations without storing a second tag definition;
    Pydantic owns generic class creation and field substitution. Polymorphic dispatch is provided by
    ``build_adapter`` and ``bind_variants``, not by direct validation of the family model.
    Fields declared as open families serialize with the runtime class. Omitted tag
    defaults remain unset, as in BaseModel. Choose IdentityDiscriminatedModel at
    the family root to retain bound tags in exclude_unset exports instead.
    """

    # Shared derived names for copy protection and optional identity marking.
    _bound_tag_names: ClassVar[tuple[str, ...]] = ()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        """Copy using native semantics, rejecting updates to bound discriminator fields.

        Even the same tag value is rejected. Other updates remain unvalidated,
        as in BaseModel.model_copy; this is not a replacement for validation.
        Only this copy operation checks updates; constructors and serializers
        acquire no new callbacks or field scans.
        """
        if update:
            for name in type(self)._bound_tag_names:
                if name in update:
                    raise ValueError(
                        f"{_model_name(type(self))}.{name}: model_copy(update=...) cannot "
                        "update a bound discriminator; omit it from update"
                    )
        return super().model_copy(update=update, deep=deep)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, source: type[BaseModel], handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        """Derive native defaults from specialized fields before schema compilation.

        Pydantic owns validation, serialization, and generic substitution. Bound
        defaults validate like explicit inputs; open tags remain required.
        Omitted defaults retain native unset semantics. No marking validator or
        runtime policy branch is registered by this base. The hook never mutates
        model_fields or an already compiled validator/serializer.
        """
        schema = _with_tag_defaults(handler(source), cls)
        schema = _with_runtime_serialization(schema, cls, handler)
        cls._bound_tag_names = _bound_names(cls)
        return schema

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        """Check native fields and inheritance without retaining a second tag definition.

        Native specializations either bind one Literal tag or remain open views.
        Named subclasses preserve fixed ancestor tags and optionally declare a
        new tag_field. Invalid fields, inheritance, or tag declarations raise
        TypeError. Remaining kwargs follow Pydantic's custom-hook contract; model
        configuration has already been consumed.
        """
        super().__pydantic_init_subclass__(**kwargs)
        # Also initialize deferred models, including explicit model_construct calls.
        # Schema generation/rebuild refreshes this same derived tuple.
        cls._bound_tag_names = _bound_names(cls)
        parents = [base for base in cls.__bases__ if issubclass(base, DiscriminatedModel)]
        if len(parents) != 1:
            raise TypeError(
                f"{_model_name(cls)}: exactly one DiscriminatedModel parent is supported; "
                f"got {[_model_name(base) for base in parents]!r}. "
                "Inherit from one family and use ordinary mixins for shared behavior."
            )
        parent = parents[0]
        inherited_fields = _tag_fields(parent)
        for name, field in inherited_fields.items():
            if name in cls.model_fields and _tag_validator(cls.model_fields[name]) is not (
                _tag_validator(field)
            ):
                raise TypeError(
                    f"{_model_name(cls)}.{name}: cannot override the inherited tag validator "
                    f"from {_model_name(parent)}; the family declaring the field owns its rule"
                )
        metadata = cls.__pydantic_generic_metadata__
        origin = metadata["origin"]
        if origin is not None:
            # Native Pydantic owns field substitution and specialization caching.
            assert issubclass(origin, DiscriminatedModel)
            field_name = _open_discriminator(origin)
            if field_name is None:
                raise TypeError(
                    f"{_model_name(cls)}: only an open discriminator family can be specialized; "
                    f"{_model_name(origin)} has no open tag_field(). "
                    "Specialize the family that declares the next discriminator."
                )
            annotation = cls.model_fields[field_name].annotation
            if not _is_open_tag(annotation):
                _literal_tag(cls, field_name)
            cls._check_tag_field(field_name)
            return
        parent_discriminator = _open_discriminator(parent)
        if parent_discriminator is not None and not _decorated_field(
            parent.model_fields[parent_discriminator]
        ):
            family = parent.__pydantic_generic_metadata__["origin"] or parent
            raise TypeError(
                f"{_model_name(cls)}.{parent_discriminator}: the inherited discriminator "
                f"from {_model_name(family)} is still open. "
                f"Inherit from {family.__name__}[Literal['tag']] to select its discriminator."
            )
        for name in inherited_fields:
            if name not in cls.model_fields:
                raise TypeError(
                    f"{_model_name(cls)}.{name}: the inherited discriminator from "
                    f"{_model_name(parent)} cannot be removed. Keep the inherited model field."
                )
            if _is_open_tag(parent.model_fields[name].annotation):
                # Decorators run after class creation. Keep an inherited open
                # decorator field pending until @model binds it. Assembly
                # rejects any model whose discriminator is still open.
                cls._check_tag_field(name)
                annotation = cls.model_fields[name].annotation
                if not _is_open_tag(annotation):
                    _literal_tag(cls, name)
                elif annotation is not str or not _decorated_field(cls.model_fields[name]):
                    raise TypeError(f"{_model_name(cls)}.{name}: preserve the open family field")
                continue
            inherited_tag = _literal_tag(parent, name)
            tag = _literal_tag(cls, name)
            if tag != inherited_tag:
                raise TypeError(
                    f"{_model_name(cls)}.{name}: the inherited discriminator cannot be overridden; "
                    f"{_model_name(parent)} fixes it to {inherited_tag!r}, got {tag!r}. "
                    "Keep the inherited tag or specialize a different branch of its family."
                )
            cls._check_tag_field(name)
        candidates = [name for name in _tag_fields(cls) if name not in inherited_fields]
        if candidates and any(
            _is_open_tag(cls.model_fields[name].annotation) for name in inherited_fields
        ):
            raise TypeError(
                f"{_model_name(cls)}: bind the inherited discriminator before introducing "
                "another family; use @model(tag='field', variant='value')"
            )
        if len(candidates) > 1:
            raise TypeError(
                f"{_model_name(cls)}: found new tag fields {candidates!r}. "
                "Declare exactly one new tag_field per family; introduce another "
                "discriminator in a child family."
            )
        discriminator = candidates[0] if candidates else None
        if discriminator is not None:
            parameters = metadata["parameters"]
            field = cls.model_fields[discriminator]
            if _decorated_field(field):
                if parameters or field.annotation is not str:
                    raise TypeError(
                        f"{_model_name(cls)}.{discriminator}: a decorated family must "
                        "introduce one non-generic string discriminator"
                    )
                cls._check_tag_field(discriminator)
                return
            if (
                len(parameters) != 1
                or cast(object, field.annotation) is not parameters[0]
                or parameters[0].__bound__ is not str
            ):
                raise TypeError(
                    f"{_model_name(cls)}.{discriminator}: Declare one str-bound type parameter "
                    f"as the discriminator field; got annotation {field.annotation!r} "
                    f"and type parameters {parameters!r}. Example: "
                    f"class {cls.__name__}[Tag: str](...): {discriminator}: Tag = tag_field()"
                )
            cls._check_tag_field(discriminator)
        elif metadata["parameters"]:
            raise TypeError(
                f"{_model_name(cls)}: a generic family must declare its discriminator "
                f"with tag_field(); no marked field was found. Example: "
                f"kind: {metadata['parameters'][0].__name__} = tag_field()"
            )

    @classmethod
    def _check_tag_field(cls, name: str) -> None:
        """Require a frozen tag field with our default factory and no alias/exclusion.

        Raise TypeError for unsupported Field settings. Runtime freezing prevents
        retagging; it does not impose covariance or a static read-only guarantee.
        """
        field = cls.model_fields[name]
        unsupported = [
            setting
            for setting, invalid in (
                ("default_factory", field.default_factory is not _tag_default),
                ("frozen", field.frozen is not True),
                ("alias", field.alias is not None),
                ("validation_alias", field.validation_alias is not None),
                ("serialization_alias", field.serialization_alias is not None),
                ("exclude", bool(field.exclude)),
                ("exclude_if", getattr(field, "exclude_if", None) is not None),
            )
            if invalid
        ]
        if unsupported:
            raise TypeError(
                f"{_model_name(cls)}.{name}: must use tag_field() without changing its factory, "
                f"freezing, aliases, or exclusion. Unsupported settings: {', '.join(unsupported)}. "
                "Remove those overrides or keep the inherited tag field."
            )


class IdentityDiscriminatedModel(DiscriminatedModel):
    """Treat bound discriminator tags as set identity fields, even when omitted.

    Choose this base when exclude_unset exports must retain routing tags. It adds
    one after-validator using Pydantic's public API; ordinary defaults and other
    exclusion options keep native semantics. Validator ordering follows Pydantic's native MRO.
    The derived name tuple contains no tag values and is refreshed on schema
    generation/rebuild; model_fields remains the authoritative declaration.
    """

    @model_validator(mode="after")
    def _mark_bound_tags(self) -> Self:
        """Mark type identity using public APIs; repeated nested validation is idempotent."""
        self.model_fields_set.update(type(self)._bound_tag_names)
        return self


def _decorator_model(value: object) -> type[DiscriminatedModel]:
    if not isinstance(value, type) or not issubclass(value, DiscriminatedModel):
        raise TypeError(f"Decorators require a DiscriminatedModel class; got {value!r}")
    if value.__pydantic_generic_metadata__["parameters"]:
        raise TypeError(f"{_model_name(value)}: specialize explicit generic parameters first")
    return value


def _extend[T: DiscriminatedModel](model: type[T], fields: dict[str, Any]) -> type[T]:
    """Use native inheritance so payload fields, methods and super() keep working."""
    return create_model(
        model.__name__,
        __base__=model,
        __module__=model.__module__,
        __qualname__=model.__qualname__,
        __doc__=model.__doc__,
        **fields,
    )


def model[T: DiscriminatedModel](
    *,
    tag: str | None = None,
    variant: str | None = None,
    validator: Callable[[str], None] | None = None,
) -> Callable[[type[T]], type[T]]:
    """Bind a parent variant, introduce a tag field, or do both at declaration time.

    ``variant`` is the value of the inherited open discriminator; ``tag`` is the
    name of a new open string field on this level. At least one is required.
    Both together bind the parent first, then introduce the next family.
    ``validator`` requires ``tag`` and checks future values of that new field;
    the parent family's validator checks ``variant``. See ``tag_field`` for the
    callable contract.

    Ordinary fields and methods retain their static types. Generated tag fields
    are runtime-only; no generic parameter, stub or whole-model Any is introduced.
    Finalized fields own tags and defaults. Assembly checks cross-type conflicts;
    construction performs no decorator reflection or routing work.
    """
    if validator is not None and tag is None:
        raise ValueError("validator requires tag= to declare a new family field")
    if tag is None and variant is None:
        raise ValueError("@model requires at least one of tag or variant")
    if tag is not None and (
        not isinstance(cast(object, tag), str) or not tag or tag.startswith("_")
    ):
        raise ValueError("tag must be a non-empty field name without a leading underscore")
    if variant is not None and (not isinstance(cast(object, variant), str) or not variant):
        raise ValueError("variant must be a non-empty string")

    def decorate(cls: type[T]) -> type[T]:
        _decorator_model(cls)
        inherited = _open_discriminator(cls)
        fields: dict[str, Any] = {}
        if variant is not None:
            if inherited is None:
                raise TypeError(
                    f"{_model_name(cls)}: variant= requires an open family discriminator"
                )
            fields[inherited] = (cast(Any, Literal)[variant], cls.model_fields[inherited])
        elif inherited is not None:
            raise TypeError(
                f"{_model_name(cls)}.{inherited}: bind the inherited discriminator "
                "before introducing another family; supply variant= to @model"
            )
        if tag is not None:
            if tag in cls.model_fields or hasattr(cls, tag):
                raise TypeError(
                    f"{_model_name(cls)}.{tag}: discriminator conflicts with "
                    "an existing field or attribute"
                )
            field = cast(FieldInfo, tag_field(validator=validator))
            field.metadata.append(_DECORATED_TAG)
            fields[tag] = (str, field)
        replacement = _extend(cls, fields)
        _replaced[cls] = replacement
        return replacement

    return decorate


def _declared_discriminator(model: type[DiscriminatedModel]) -> str | None:
    """Identify family owners, excluding pending subclasses that only inherit a tag."""
    name = _open_discriminator(model)
    if name is None:
        return None
    for parent in model.__bases__:
        if issubclass(parent, DiscriminatedModel) and name in _tag_fields(parent):
            return None
    return name


def _branches(
    variants: Iterable[object],
) -> dict[type[DiscriminatedModel], dict[str, type[DiscriminatedModel]]]:
    """Build a local dispatch table from leaf fields and native inheritance.

    Unspecialized ancestors identify the family boundaries. All tag values come
    from the supplied leaf's resolved Pydantic fields, not generic arguments or
    stored paths. Repeated classes are harmless; conflicting parent/tag claims
    raise ValueError. Abstract models, open families and untagged models raise TypeError.
    """
    branches: dict[type[DiscriminatedModel], dict[str, type[DiscriminatedModel]]] = {}
    seen: set[type[DiscriminatedModel]] = set()
    for variant in variants:
        if not isinstance(variant, type) or not issubclass(variant, DiscriminatedModel):
            raise TypeError(f"Expected a DiscriminatedModel variant class; got {variant!r}")
        if variant in seen:
            continue
        seen.add(variant)
        if (
            isabstract(variant)
            or _open_discriminator(variant) is not None
            or not _tag_fields(variant)
        ):
            raise TypeError(f"{_model_name(variant)} must be a tagged leaf model")
        families = [
            base
            for base in reversed(variant.__mro__)
            if issubclass(base, DiscriminatedModel)
            and base.__pydantic_generic_metadata__["origin"] is None
            and _declared_discriminator(base) is not None
        ]
        for index, parent in enumerate(families):
            name = _open_discriminator(parent)
            assert name is not None
            tag = _literal_tag(variant, name)
            child = families[index + 1] if index + 1 < len(families) else variant
            children = branches.setdefault(parent, {})
            previous = children.get(tag)
            if previous is not None and previous is not child:
                raise ValueError(
                    f"Duplicate tag {tag!r} for {_model_name(parent)}.{name}: "
                    f"{_model_name(previous)} and {_model_name(child)} claim the same branch. "
                    "Use distinct Literal tags or include only one of these branches in variants."
                )
            children[tag] = child
    return branches


def family_base(family: object) -> type[DiscriminatedModel]:
    """Normalize open generic views while preserving concrete class ancestry.

    Named classes and closed specializations retain their identity and bound
    tags. Only open native views such as Family[Any] use their generic origin.
    Require a model class; adapter construction separately requires an open tag.
    """
    if not isinstance(family, type) or not issubclass(family, DiscriminatedModel):
        raise TypeError(f"family must be a DiscriminatedModel class; got {family!r}")
    origin = family.__pydantic_generic_metadata__["origin"]
    if origin is not None and _open_discriminator(family) is not None:
        assert issubclass(origin, DiscriminatedModel)
        return origin
    return family


def _union_for(
    family: object,
    branches: dict[type[DiscriminatedModel], dict[str, type[DiscriminatedModel]]],
) -> Any:
    """Assemble nested Annotated unions; let Pydantic generate all schemas.

    Dynamic type-expression assembly is the only untyped boundary here. Each
    leaf retains its model class; no model or compiled schema is modified.
    """
    if (
        not isinstance(family, type)
        or not issubclass(family, DiscriminatedModel)
        or _open_discriminator(family) is None
    ):
        raise TypeError(
            f"Expected an open DiscriminatedModel family; got {family!r}. "
            "Apply the binding to the family itself: "
            "use Annotated[Family[Any], binding] | None for an optional field."
        )
    origin = family_base(family)
    discriminator = _open_discriminator(origin)
    assert discriminator is not None
    children = branches.get(origin)
    if not children:
        raise ValueError(f"No variants supplied for {_model_name(origin)}")
    choices: list[Any] = [
        _union_for(child, branches) if _open_discriminator(child) is not None else child
        for child in children.values()
    ]
    union: Any = choices[0]
    for choice in choices[1:]:
        union = union | choice
    return cast(Any, Annotated)[union, Field(discriminator=discriminator)]


def concrete_model(value: object) -> type[DiscriminatedModel]:
    """Validate a concrete behavior target without compiling a data union."""
    if not isinstance(value, type) or not issubclass(value, DiscriminatedModel):
        raise TypeError(f"Expected a DiscriminatedModel class; got {value!r}")
    if isabstract(value) or _open_discriminator(value) is not None:
        raise TypeError(
            f"{_model_name(value)}: expected a concrete model, not an open/abstract family"
        )
    return value


# Assembly-time binding of nested open families through native Pydantic schemas.
#
# A scope (one ``build_adapter`` call or one ``bind_variants`` annotation) binds every
# open-family field its leaves reach to that family's supplied leaves. Scope
# compilation takes the declared classes' own schemas, points each open-family
# position at the scope's union for that family, and compiles the result with a
# forced rebuild. Pydantic reuses a complete model's prebuilt validator for nested
# model nodes on ordinary builds; since Pydantic 2.13 a forced rebuild compiles them
# from the supplied schema instead (pydantic/pydantic#12689). Declared classes,
# their validators and hooks are used as they are and are never modified.

type _Branches = dict[type[DiscriminatedModel], dict[str, type[DiscriminatedModel]]]

_UNASSEMBLED = "typebind_unassembled"
_BINDING_KEY = "typebind_binding"
_WRAPPERS = frozenset({"function-before", "function-after", "function-wrap"})
# Core-schema keys holding data or callables rather than schemas; walks never enter them.
_DATA_KEYS = frozenset(
    {
        "cls",
        "config",
        "custom_error_context",
        "default",
        "default_factory",
        "discriminator",
        "expected",
        "function",
        "members",
        "metadata",
        "post_init",
    }
)
# Core-schema keys holding maps keyed by user field names or tags.
_MAP_KEYS = frozenset({"fields", "choices"})
_scope_ids = count()


def _leaves(
    family: type[DiscriminatedModel], branches: _Branches
) -> Iterator[type[DiscriminatedModel]]:
    for child in branches.get(family, {}).values():
        if _open_discriminator(child) is not None:
            yield from _leaves(child, branches)
        else:
            yield child


def _family_of(value: object) -> type[DiscriminatedModel] | None:
    """The family an open-family position refers to, or None for other types."""
    value = _resolve_replaced(value)
    return family_base(value) if _is_open_family(value) else None


# Eagerness: whether a scope's leaves reach a position it must bind. Decided from
# resolved field annotations with call-local state only, at schema-build time.


def _evaluate(annotation: str | ForwardRef, owner: type) -> object | None:
    text = annotation if isinstance(annotation, str) else annotation.__forward_arg__
    module = sys.modules.get(owner.__module__)
    namespace = {**(vars(module) if module else {}), owner.__name__: owner}
    try:
        return eval(text, namespace)  # noqa: S307 - a declared annotation, as Pydantic does
    except Exception:
        return None


def _annotation_reaches(annotation: object, owner: type, seen: set[object]) -> bool:
    annotation = _unalias(annotation)
    if isinstance(annotation, str | ForwardRef):
        annotation = _evaluate(annotation, owner)
    if get_origin(annotation) is Literal:
        return False
    if get_origin(annotation) is Annotated:
        inner = get_args(annotation)[0]
        for item in cast(Any, annotation).__metadata__:
            binding = _scope_marker(item)
            if isinstance(binding, _Binding):
                return binding.reaches(inner, seen)
        return _annotation_reaches(inner, owner, seen)
    if _family_of(annotation) is not None:
        return True
    resolved = _resolve_replaced(annotation)
    if isinstance(resolved, type) and issubclass(resolved, BaseModel):
        return _model_reaches(resolved, seen)
    return any(_annotation_reaches(arg, owner, seen) for arg in get_args(annotation))


def _model_reaches(model: type[BaseModel], seen: set[object]) -> bool:
    if model in seen:
        return False
    seen.add(model)
    for field in model.model_fields.values():
        bindings = [b for b in map(_scope_marker, field.metadata) if isinstance(b, _Binding)]
        if bindings:
            if bindings[0].reaches(field.annotation, seen):
                return True
        elif _annotation_reaches(field.annotation, model, seen):
            return True
    return False


# Schema compilation.


def _map_schema(item: dict[str, Any], transform: Callable[[Any], Any]) -> dict[str, Any]:
    """Apply ``transform`` to the schema-holding children of a node, keeping its shape."""
    out: dict[str, Any] = {}
    for key, value in item.items():
        if key in _MAP_KEYS and isinstance(value, dict):
            out[key] = {
                name: transform(entry) for name, entry in cast(dict[str, Any], value).items()
            }
        elif key in _DATA_KEYS:
            out[key] = value
        else:
            out[key] = transform(value)
    return out


def _class_chain(model: type) -> list[Any] | None:
    """The validator wrappers of a model class's own schema, outermost first.

    A class whose build is deferred has no stored schema yet; its schema is then
    generated by a separate adapter, which leaves the class itself untouched.
    """
    schema: object = vars(model).get("__pydantic_core_schema__")
    if not isinstance(schema, dict):
        # Inspect outside the active compilation, so explicit bindings in the class
        # keep their unassembled form and nothing is added to the current scope.
        token = _active.set(None)
        try:
            adapter = TypeAdapter[Any](model)
            # The adapter inherits the deferral; build it, not the class.
            complete = adapter.rebuild(force=True, raise_errors=True)
            schema = adapter.core_schema if complete else None
        except Exception:
            return None
        finally:
            _active.reset(token)
        if not isinstance(schema, dict):
            return None
    node = cast(dict[str, Any], schema)
    definitions: dict[str, Any] = {}
    if node.get("type") == "definitions":
        definitions = {item["ref"]: item for item in node["definitions"]}
        node = node["schema"]
    chain: list[Any] = []
    while True:
        if node.get("type") == "definition-ref":
            node = definitions.get(node["schema_ref"], {})
        elif node.get("type") in _WRAPPERS and "schema" in node:
            chain.append(node.get("function"))
            node = node["schema"]
        else:
            return chain if node.get("type") == "model" else None


def _position(node: Any, definitions: dict[str, Any]) -> type[DiscriminatedModel] | None:
    """The family of an open-family position, or None.

    A position is a model node, a reference to one, or a model wrapped exactly by its
    class's own validator chain. Any other wrapper, such as a field or alias
    validator, is not the class schema; the position lies inside it.
    """
    wrappers: list[Any] = []
    while isinstance(node, dict):
        item = cast(dict[str, Any], node)
        kind = item.get("type")
        if kind == "definition-ref":
            node = definitions.get(item["schema_ref"])
        elif kind in _WRAPPERS and "schema" in item:
            wrappers.append(item.get("function"))
            node = item["schema"]
        else:
            break
    if not isinstance(node, dict):
        return None
    item = cast(dict[str, Any], node)
    if item.get("type") != "model":
        return None
    family = _family_of(item["cls"])
    if family is None or wrappers != (_class_chain(item["cls"]) or []):
        return None
    return family


def _closed(node: Any) -> tuple[_Binding, Any] | None:
    """The explicit binding of an unassembled nested-family field, or None."""
    if isinstance(node, dict):
        metadata: object = cast(dict[str, Any], node).get("metadata")
        if isinstance(metadata, dict):
            binding = cast(dict[str, Any], metadata).get(_BINDING_KEY)
            if binding is not None:
                return cast(tuple[_Binding, Any], binding)
    return None


def _declaring(model: type, name: str) -> type | None:
    """The most basic model class in the MRO that declares field ``name``."""
    for base in reversed(model.__mro__):
        if issubclass(base, BaseModel) and base is not BaseModel and name in base.model_fields:
            return base
    return None


class _Scan:
    """Positions reached from one family union, with their Model.field paths."""

    def __init__(self, definitions: dict[str, Any]) -> None:
        self.definitions = definitions
        self.positions: list[tuple[type[DiscriminatedModel], str]] = []
        self.closed: list[tuple[_Binding, Any]] = []
        self.visited: set[str] = set()
        self.owner: type | None = None

    def walk(self, node: Any, where: str) -> None:
        if isinstance(node, list | tuple):
            for item in cast(list[Any], node):
                self.walk(item, where)
            return
        if not isinstance(node, dict):
            return
        item = cast(dict[str, Any], node)
        closed = _closed(item)
        family = None if closed else _position(item, self.definitions)
        if closed or family is not None:
            if closed:
                self.closed.append(closed)
            else:
                assert family is not None
                self.positions.append((family, where))
            return
        kind = item.get("type")
        if kind == "definition-ref":
            ref = item["schema_ref"]
            if ref not in self.visited and ref in self.definitions:
                self.visited.add(ref)
                self.walk(self.definitions[ref], where)
            return
        if kind == "model":
            self.owner = cast(type, item["cls"])
            self.walk(item["schema"], self.owner.__qualname__)
            return
        if kind == "model-fields":
            owner = self.owner
            for name, field in cast(dict[str, Any], item["fields"]).items():
                declaring = _declaring(owner, name) if owner is not None else None
                self.walk(field, f"{declaring.__qualname__ if declaring else where}.{name}")
            return
        for key, value in item.items():
            if key == "serialization":
                continue
            if key in _MAP_KEYS and isinstance(value, dict):
                for entry in cast(dict[str, Any], value).values():
                    self.walk(entry, where)
            elif key not in _DATA_KEYS:
                self.walk(value, where)


def _local(node: Any, definitions: dict[str, Any], refs: set[str], models: list[Any]) -> bool:
    """Whether a node binds a position without following references; collect both."""
    if isinstance(node, list | tuple):
        found = False
        for item in cast(list[Any], node):
            found = _local(item, definitions, refs, models) or found
        return found
    if not isinstance(node, dict):
        return False
    item = cast(dict[str, Any], node)
    if _closed(item) or _position(item, definitions) is not None:
        return True
    if item.get("type") == "definition-ref":
        refs.add(item["schema_ref"])
        return False
    if item.get("type") == "model":
        models.append(item)
    found = False
    for key, value in item.items():
        if key == "serialization":
            continue
        children = (
            list(cast(dict[str, Any], value).values())
            if key in _MAP_KEYS and isinstance(value, dict)
            else []
            if key in _DATA_KEYS
            else [value]
        )
        for child in children:
            found = _local(child, definitions, refs, models) or found
    return found


def _custom_init_models(roots: list[Any], definitions: dict[str, Any]) -> list[type]:
    """Models on a rebound path that Pydantic would construct through their own __init__.

    Only definitions the scope's unions actually reach are considered; a family
    position is replaced by its union, so the declared family body is not reached.
    Whether a reached definition leads to a rebound position is a fixed point over
    references, so every path into a shared or recursive definition counts.
    """
    models: list[Any] = []
    direct: dict[str, tuple[bool, set[str]]] = {}
    pending: set[str] = set()
    _local(roots, definitions, pending, models)
    while pending:
        ref = pending.pop()
        if ref in direct or ref not in definitions:
            continue
        refs: set[str] = set()
        direct[ref] = (_local(definitions[ref], definitions, refs, models), refs)
        pending |= refs - direct.keys()
    reaching = {ref for ref, (found, _) in direct.items() if found}
    changed = True
    while changed:
        changed = False
        for ref, (_, refs) in direct.items():
            if ref not in reaching and refs & reaching:
                reaching.add(ref)
                changed = True
    rejected: list[type] = []
    for model in models:
        refs = set[str]()
        if model.get("custom_init") and (
            _local(model["schema"], definitions, refs, []) or refs & reaching
        ):
            if model["cls"] not in rejected:
                rejected.append(model["cls"])
    return rejected


class _Compilation:
    """One assembly: the definitions of every scope it compiles."""

    def __init__(self) -> None:
        self.collecting = False
        self.definitions: list[CoreSchema] = []
        self.closed_refs: dict[tuple[int, type[DiscriminatedModel]], str] = {}

    def closed_ref(self, binding: _Binding, root: object) -> str:
        family = family_base(root)
        key = (id(binding), family)
        ref = self.closed_refs.get(key)
        if ref is None:
            scope = f"typebind{next(_scope_ids)}"
            ref = self.closed_refs[key] = _family_ref(scope, family)
            self.compile(binding.branches, family, scope)
        return ref

    def compile(self, branches: _Branches, root: type[DiscriminatedModel], scope: str) -> str:
        """Add a scope's definitions; return the reference of its root family union."""
        families = [root]
        while True:
            unions = tuple(_union_for(family, branches) for family in families)
            previous, self.collecting = self.collecting, True
            try:
                adapter = TypeAdapter[Any](cast(Any, tuple)[unions])
                adapter.rebuild(force=True, raise_errors=True)
                schema = cast(dict[str, Any], adapter.core_schema)
            finally:
                self.collecting = previous
            definitions: dict[str, Any] = {}
            if schema["type"] == "definitions":
                definitions = {item["ref"]: item for item in schema["definitions"]}
                schema = schema["schema"]
            items: list[Any] = schema["items_schema"]
            scans = [_Scan(definitions) for _ in families]
            for scan, item, family in zip(scans, items, families, strict=True):
                scan.walk(item, _model_name(family))
            found: list[type[DiscriminatedModel]] = []
            for scan in scans:
                for family, where in scan.positions:
                    if not branches.get(family):
                        raise ValueError(
                            f"No variants supplied for {_model_name(family)}, reached at {where}"
                        )
                    if family not in families and family not in found:
                        found.append(family)
            if not found:
                break
            families.extend(found)
        _reject_cycles(families, scans)
        for model in _custom_init_models(items, definitions):
            raise TypeError(
                f"{_model_name(model)} defines a custom __init__, which Pydantic calls "
                "instead of the scope's bound validator. Remove the initializer, or "
                "narrow its family fields in their declaration."
            )
        for scan in scans:
            for binding, root_view in scan.closed:
                self.closed_ref(binding, root_view)

        def rewrite(node: Any) -> Any:
            if isinstance(node, list):
                return [rewrite(item) for item in cast(list[Any], node)]
            if isinstance(node, tuple):
                return tuple(rewrite(item) for item in cast(tuple[Any], node))
            if not isinstance(node, dict):
                return node
            item = cast(dict[str, Any], node)
            closed_binding = _closed(item)
            if closed_binding is not None:
                return core_schema.definition_reference_schema(self.closed_ref(*closed_binding))
            family = _position(item, definitions)
            if family is not None:
                return core_schema.definition_reference_schema(_family_ref(scope, family))
            out = _map_schema(item, rewrite)
            if "ref" in out:
                out["ref"] = f"{scope}:{out['ref']}"
            if out.get("type") == "definition-ref":
                out["schema_ref"] = f"{scope}:{out['schema_ref']}"
            return out

        self.definitions.extend(
            rewrite(item) for item in definitions.values() if _position(item, definitions) is None
        )
        for family, item in zip(families, items, strict=True):
            union = cast(CoreSchema, {**rewrite(item), "ref": _family_ref(scope, family)})
            self.definitions.append(union)
        return _family_ref(scope, root)

    def schema(self, ref: str) -> CoreSchema:
        return core_schema.definitions_schema(
            core_schema.definition_reference_schema(ref), list(self.definitions)
        )


def _family_ref(scope: str, family: type[DiscriminatedModel]) -> str:
    # JSON Schema names a definition after the reference up to its last colon.
    return f"{_model_name(family)}Variants:{scope}"


def _reject_cycles(families: list[type[DiscriminatedModel]], scans: list[_Scan]) -> None:
    """Reject families that reach themselves through automatically bound fields."""
    edges = {family: scan.positions for family, scan in zip(families, scans, strict=True)}
    state: dict[type[DiscriminatedModel], int] = {}
    path: list[str] = []

    def visit(family: type[DiscriminatedModel]) -> None:
        state[family] = 1
        for target, where in edges.get(family, []):
            path.append(where)
            if state.get(target) == 1:
                raise TypeError(
                    "Cycle through automatically bound fields: "
                    f"{' -> '.join([*path, _model_name(target)])}. Declare the recursive "
                    "field explicitly with Annotated[Family, bind_variants(...)]."
                )
            if target not in state:
                visit(target)
            path.pop()
        state[family] = 2

    for family in families:
        if family not in state:
            visit(family)


_active: ContextVar[_Compilation | None] = ContextVar("typebind_compilation", default=None)


_UNASSEMBLED_MESSAGE = (
    "The binding for {family} reaches nested open families and was not compiled by "
    "typebind.rebuild_bindings(); pass its model to rebuild_bindings() or use build_adapter()"
)


def _unassembled_context(root: object) -> dict[str, str]:
    return {"family": root.__qualname__ if isinstance(root, type) else repr(root)}


def _unassembled_schema(binding: _Binding, root: object) -> CoreSchema:
    """A field that always fails, so an unassembled binding never parses base classes."""
    context = _unassembled_context(root)

    def validate(value: object, info: core_schema.ValidationInfo) -> object:
        raise PydanticCustomError(_UNASSEMBLED, _UNASSEMBLED_MESSAGE, context)

    return core_schema.with_info_plain_validator_function(
        validate, metadata={_BINDING_KEY: (binding, root)}
    )


class _Binding(_ScopeMarker):
    """An explicit variant snapshot bound to an Annotated family field."""

    def __init__(self, variants: Iterable[type[DiscriminatedModel]]) -> None:
        self.branches = _branches(variants)

    def reaches(self, root: object, seen: set[object]) -> bool:
        """Whether this binding, at ``root``, reaches positions it must bind.

        Decided per annotated root family, so leaves of unrelated families in the
        same snapshot do not matter.
        """
        family = _family_of(root)
        if family is None or (self, family) in seen:
            return False
        seen.add((self, family))
        return any(_model_reaches(leaf, seen) for leaf in _leaves(family, self.branches))

    def json_schema(self, schema: CoreSchema, handler: GetJsonSchemaHandler) -> JsonSchemaValue:
        """Refuse to describe an unassembled field rather than describe base classes."""
        closed = _closed(schema)
        if closed is not None:
            raise TypeError(_UNASSEMBLED_MESSAGE.format(**_unassembled_context(closed[1])))
        return handler(schema)

    def core_schema(self, source: Any, handler: GetCoreSchemaHandler) -> CoreSchema:
        union = _union_for(source, self.branches)
        if not self.reaches(source, set()):
            return handler.generate_schema(union)
        compilation = _active.get()
        if compilation is None or compilation.collecting:
            return _unassembled_schema(self, source)
        schema = compilation.schema(compilation.closed_ref(self, source))
        return handler.generate_schema(_SchemaCarrier(schema))


class _SchemaCarrier:
    """Hand a complete schema, including its definitions, to Pydantic generation."""

    def __init__(self, schema: CoreSchema) -> None:
        self.schema = schema

    def __get_pydantic_core_schema__(
        self, source: Any, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        return self.schema


def bind_variants(variants: Iterable[type[DiscriminatedModel]]) -> GetPydanticSchema:
    """Bind a variant snapshot explicitly to an Annotated family field.

    Use ``Annotated[Family[Any], bind_variants(leaves)]`` in a model field or a
    container annotation, then use ordinary Pydantic validation and serialization.
    The static field type remains the declared family; runtime leaves do not
    synthesize a static union. Known sets can instead use native Annotated unions.

    The binding is a closed scope: its leaves bind every open family their fields
    reach, and enclosing scopes do not rebind inside it. When its root reaches no
    such field, it compiles in place as before. Otherwise its model must be passed to
    ``rebuild_bindings``; until then the field raises ``typebind_unassembled``.

    Duplicate parent/tag mappings raise ValueError. Pydantic owns discriminator
    schema generation and its native restrictions, including tag-field validators.
    The snapshot is independent of subsequent changes to the input iterable.
    """
    binding = _Binding(variants)
    return GetPydanticSchema(binding.core_schema, binding.json_schema)


def _carrier(root: type[DiscriminatedModel], schema: CoreSchema) -> type[BaseModel]:
    """A scope-local model compiling ``schema`` with a forced rebuild."""

    def generate(cls: type[BaseModel], source: Any, handler: GetCoreSchemaHandler) -> CoreSchema:
        return schema

    metaclass = cast(Any, type(BaseModel))
    carrier = cast(
        type[BaseModel],
        metaclass(
            root.__name__,
            (BaseModel,),
            {
                "__module__": root.__module__,
                "__qualname__": root.__qualname__,
                "model_config": ConfigDict(defer_build=True),
                "__get_pydantic_core_schema__": classmethod(generate),
            },
        ),
    )
    carrier.model_rebuild(force=True)
    return carrier


def build_adapter[T: DiscriminatedModel](
    family: type[T], *, variants: Iterable[type[DiscriminatedModel]]
) -> TypeAdapter[T]:
    """Build a native TypeAdapter for one open family's nested union.

    ``family`` supplies the static result type; its variant paths supply the
    runtime union. Every open-family field the supplied leaves reach, including
    fields in containers and nested models, dispatches through that family's
    supplied leaves. Leaves of families the scope never reaches are ignored.
    Conflicting tags, missing branches and reached families without variants raise
    ValueError; cycles through automatically bound fields and models with a custom
    ``__init__`` on a rebound path raise TypeError. Closed leaf models and enclosing
    containers are not family roots and raise TypeError.

    No declared class or compiled validator is modified. Results are instances of
    the declared classes; JSON Schema, validation and serialization use one
    compiled schema.
    """
    branches = _branches(variants)
    union = _union_for(family, branches)
    root = family_base(family)
    seen: set[object] = set()
    if not any(_model_reaches(leaf, seen) for leaf in _leaves(root, branches)):
        return TypeAdapter[T](union)
    compilation = _Compilation()
    token = _active.set(compilation)
    try:
        schema = compilation.schema(
            compilation.compile(branches, root, f"typebind{next(_scope_ids)}")
        )
    finally:
        _active.reset(token)
    return cast(TypeAdapter[T], TypeAdapter(_carrier(root, schema)))


def _unassembled(node: Any) -> int:
    """The number of unassembled bindings left in a compiled schema."""
    if isinstance(node, list | tuple):
        return sum(_unassembled(item) for item in cast(list[Any], node))
    if not isinstance(node, dict):
        return 0
    item = cast(dict[str, Any], node)
    if _closed(item) is not None:
        return 1
    count = 0
    for key, value in item.items():
        if key in _MAP_KEYS and isinstance(value, dict):
            count += sum(_unassembled(entry) for entry in cast(dict[str, Any], value).values())
        elif key not in _DATA_KEYS:
            count += _unassembled(value)
    return count


def _rebuild_one(model: type[BaseModel]) -> None:
    token = _active.set(_Compilation())
    try:
        model.model_rebuild(force=True)
    except Exception as error:
        _active.reset(token)
        token = None
        try:
            model.model_rebuild(force=True)
        except Exception as restore_error:
            error.add_note(f"typebind could not restore {_model_name(model)}: {restore_error!r}")
        raise
    finally:
        if token is not None:
            _active.reset(token)


def rebuild_bindings(*models: type[BaseModel]) -> None:
    """Compile models whose ``bind_variants`` fields reach nested open families.

    Each model is rebuilt with its bindings bound; the order of ``models`` does not
    matter. A model that uses another given model as a field type reuses that
    model's compiled schema, so models still holding unassembled bindings after a
    pass are rebuilt again while the number of such bindings keeps decreasing. A
    model compiled any other way keeps fields that raise ``typebind_unassembled``.
    If compiling a model fails, it is first returned to that failing state, then
    the error is re-raised. Models without such bindings are simply rebuilt.

    If rebuilding stops making progress while given models still hold unassembled
    bindings, TypeError names them. This happens when they reach a binding through
    a model that was not passed, or through mutually recursive models compiled
    before a binding container they share was assembled; Pydantic reuses those
    compiled schemas, so assemble such a container before defining them.
    """
    pending = list(models)
    left: int | None = None
    while pending:
        for model in pending:
            _rebuild_one(model)
        counts = {
            model: _unassembled(vars(model).get("__pydantic_core_schema__")) for model in pending
        }
        total = sum(counts.values())
        pending = [model for model in pending if counts[model]]
        # Recursive containers may need several passes; stop once one binds nothing more.
        if pending and left is not None and total >= left:
            names = ", ".join(_model_name(model) for model in pending)
            raise TypeError(
                f"rebuild_bindings() could not assemble {names}: they still reach an "
                "unassembled binding through another model's compiled schema. Pass every "
                "model whose bindings they reach. Pydantic reuses compiled schemas of "
                "mutually recursive models, so a binding container shared by them must be "
                "assembled with rebuild_bindings() before those models are defined."
            )
        left = total
