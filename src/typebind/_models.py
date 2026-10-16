"""Generic/decorator declarations and explicit Pydantic discriminated unions."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from inspect import isabstract
from typing import Annotated, Any, ClassVar, Literal, Self, TypeVar, cast

from pydantic import (
    BaseModel,
    Field,
    GetCoreSchemaHandler,
    GetPydanticSchema,
    TypeAdapter,
    create_model,
    model_validator,
)
from pydantic.fields import FieldInfo
from pydantic_core import CoreSchema, core_schema

# A declaration-style marker, stored only on generated fields. Tag names and
# values are still read exclusively from the finalized Pydantic model_fields.
_DECORATED_TAG = object()


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


def tag_field() -> Any:
    """Declare this level's discriminator as a native frozen Pydantic field.

    The private default factory identifies tag fields after native field merging
    and generic substitution. Schema generation replaces it with a validated
    native default for bound tags, or a required field for open tags.
    DiscriminatedModel keeps native unset semantics; IdentityDiscriminatedModel
    additionally marks bound tags as set through Pydantic's public validator API.

    Like Pydantic's Field, this helper returns Any for assignment to a typed
    field. The explicit annotation owns the field and constructor argument types;
    the assigned default makes the argument optional to static type checkers.
    """
    return Field(default_factory=_tag_default, frozen=True)


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


class DiscriminatedModel(BaseModel):
    """Declare model families with one discriminator per level.

    A family declares a str-bound type parameter and marks its field with
    ``tag_field()``. Subclasses select a tag through native
    ``Family[Literal["tag"]]`` specialization and may introduce another family.
    Alternatively, @model generates runtime-only tag fields.
    The field declaration is the sole source of its name, type, and tag behavior.

    These hooks validate declarations without storing a second tag definition;
    Pydantic owns generic class creation and field substitution. Polymorphic dispatch is provided by
    ``build_adapter``, not by direct validation of the family model. Omitted tag
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
        inherited_fields = _tag_fields(parent)
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
                ("exclude_if", field.exclude_if is not None),
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
        raise TypeError("Decorators require a DiscriminatedModel class")
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
    *, tag: str | None = None, variant: str | None = None
) -> Callable[[type[T]], type[T]]:
    """Bind a parent variant, introduce a tag field, or do both at declaration time.

    ``variant`` is the value of the inherited open discriminator; ``tag`` is the
    name of a new open string field on this level. At least one is required.
    Both together bind the parent first, then introduce the next family.

    Ordinary fields and methods retain their static types. Generated tag fields
    are runtime-only; no generic parameter, stub or whole-model Any is introduced.
    Finalized fields own tags and defaults. Assembly checks cross-type conflicts;
    construction performs no decorator reflection or routing work.
    """
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
            field = cast(FieldInfo, tag_field())
            field.metadata.append(_DECORATED_TAG)
            fields[tag] = (str, field)
        return _extend(cls, fields)

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
    variants: Iterable[type[DiscriminatedModel]],
) -> dict[type[DiscriminatedModel], dict[str, type[DiscriminatedModel]]]:
    """Build a local dispatch table from leaf fields and native inheritance.

    Unspecialized ancestors identify the family boundaries. All tag values come
    from the supplied leaf's resolved Pydantic fields, not generic arguments or
    stored paths. Repeated classes are harmless; conflicting parent/tag claims
    raise ValueError. Open families and untagged models raise TypeError.
    """
    branches: dict[type[DiscriminatedModel], dict[str, type[DiscriminatedModel]]] = {}
    for variant in dict.fromkeys(variants):
        if _open_discriminator(variant) is not None or not _tag_fields(variant):
            raise TypeError(f"{variant.__name__} must be a tagged leaf model")
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
        raise TypeError("family must be a DiscriminatedModel class")
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
        raise TypeError("Expected an open DiscriminatedModel family")
    origin = family_base(family)
    discriminator = _open_discriminator(origin)
    assert discriminator is not None
    children = branches.get(origin)
    if not children:
        raise ValueError(f"No variants supplied for {origin.__qualname__}")
    choices: list[Any] = [
        _union_for(child, branches) if _open_discriminator(child) is not None else child
        for child in children.values()
    ]
    union: Any = choices[0]
    for choice in choices[1:]:
        union = union | choice
    return cast(Any, Annotated)[union, Field(discriminator=discriminator)]


def bind_variants(variants: Iterable[type[DiscriminatedModel]]) -> GetPydanticSchema:
    """Bind a variant snapshot explicitly to an Annotated family field.

    Use ``Annotated[Family[Any], bind_variants(leaves)]`` in a model field or a
    container annotation, then use ordinary Pydantic validation and serialization.
    The static field type remains the declared family; runtime leaves do not
    synthesize a static union. Known sets can instead use native Annotated unions.

    Duplicate parent/tag mappings raise ValueError. Pydantic owns discriminator
    schema generation and its native restrictions, including tag-field validators.
    The snapshot is independent of subsequent changes to the input iterable.
    """
    branches = _branches(variants)

    def schema(source: Any, handler: GetCoreSchemaHandler) -> CoreSchema:
        return handler.generate_schema(_union_for(source, branches))

    return GetPydanticSchema(schema)


def build_adapter[T: DiscriminatedModel](
    family: type[T], *, variants: Iterable[type[DiscriminatedModel]]
) -> TypeAdapter[T]:
    """Build a native TypeAdapter for one open family's nested union.

    ``family`` supplies the static result type; its variant paths supply the
    runtime union. Conflicting tags or missing branches raise ValueError. Closed
    leaf models and enclosing containers are not family roots and raise TypeError.
    Bind container fields with bind_variants and use TypeAdapter(Container) instead.

    No adapter attributes are patched. Native rebuild, JSON Schema generation,
    validation, and serialization operate on the actual union annotation.
    """
    return TypeAdapter[T](_union_for(family, _branches(variants)))


def concrete_model(value: object) -> type[DiscriminatedModel]:
    """Validate a concrete behavior target without compiling a data union."""
    if not isinstance(value, type) or not issubclass(value, DiscriminatedModel):
        raise TypeError(f"Expected a DiscriminatedModel class; got {value!r}")
    if isabstract(value) or _open_discriminator(value) is not None:
        raise TypeError(
            f"{_model_name(value)}: expected a concrete model, not an open/abstract family"
        )
    return value
