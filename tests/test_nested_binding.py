"""Assembly-time binding of nested open families (scopes, closed bindings, errors)."""

import json
import threading
from typing import Annotated, Any, Literal, Self, cast

import pytest
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    PydanticUserError,
    SerializeAsAny,
    Tag,
    TypeAdapter,
    ValidationError,
    create_model,
    field_validator,
    model_validator,
)

from typebind import (
    DiscriminatedModel,
    IdentityDiscriminatedModel,
    bind_variants,
    build_adapter,
    model,
    rebuild_bindings,
    tag_field,
)


def assert_unassembled(error: ValidationError) -> None:
    assert [item["type"] for item in error.errors()] == ["typebind_unassembled"]
    assert "rebuild_bindings" in str(error)


# The original report: an outer family field typed as an inner family base.


@model(tag="provider")
class ModelConfig(DiscriminatedModel):
    model_id: str


@model(variant="openai")
class OpenAIModelConfig(ModelConfig):
    reasoning_effort: str | None = None


@model(variant="anthropic")
class AnthropicModelConfig(ModelConfig):
    thinking: bool = False


@model(tag="harness_type")
class SessionConfig(DiscriminatedModel):
    model: ModelConfig


@model(variant="generic")
class GenericConfig(SessionConfig):
    pass


@model(tag="provider")
class IdentityModelConfig(IdentityDiscriminatedModel):
    model_id: str


@model(variant="openai")
class IdentityOpenAI(IdentityModelConfig):
    reasoning_effort: str | None = None


@model(tag="harness_type")
class IdentitySessionConfig(IdentityDiscriminatedModel):
    model: IdentityModelConfig


@model(variant="generic")
class IdentityGenericConfig(IdentitySessionConfig):
    pass


OPENAI = {"provider": "openai", "model_id": "m", "reasoning_effort": "low"}
ANTHROPIC = {"provider": "anthropic", "model_id": "a", "thinking": True}
RAW = {"harness_type": "generic", "model": OPENAI}

CASES: list[tuple[type[DiscriminatedModel], list[type[DiscriminatedModel]]]] = [
    (SessionConfig, [GenericConfig, OpenAIModelConfig]),
    (IdentitySessionConfig, [IdentityGenericConfig, IdentityOpenAI]),
]


@pytest.mark.parametrize(("root", "variants"), CASES)
def test_nested_family_dispatches_and_round_trips(
    root: type[DiscriminatedModel], variants: list[type[DiscriminatedModel]]
) -> None:
    outer, inner = variants
    adapter = build_adapter(root, variants=variants)
    value = adapter.validate_python(RAW)
    assert type(value) is outer
    nested = value.model_dump(warnings="error")["model"]
    assert nested == {"model_id": "m", "provider": "openai", "reasoning_effort": "low"}
    assert type(cast(Any, value).model) is inner
    assert value == outer.model_validate({"model": inner(**OPENAI)})
    assert adapter.validate_json(adapter.dump_json(value, warnings="error")) == value
    assert adapter.validate_python(value.model_dump(warnings="error")) == value


@pytest.mark.parametrize(("root", "variants"), CASES)
def test_container_binding_needs_rebuild_bindings(
    root: type[DiscriminatedModel], variants: list[type[DiscriminatedModel]]
) -> None:
    envelope = create_model(
        "Envelope", config=(cast(Any, Annotated)[root, bind_variants(variants)], ...)
    )
    with pytest.raises(ValidationError) as error:
        envelope.model_validate({"config": RAW})
    assert_unassembled(error.value)
    with pytest.raises(TypeError, match="rebuild_bindings"):
        envelope.model_json_schema()

    assert rebuild_bindings(envelope) is None
    value = envelope.model_validate({"config": RAW}).model_dump()
    assert value["config"]["model"]["reasoning_effort"] == "low"
    restored = TypeAdapter(envelope).validate_python({"config": RAW})
    assert type(cast(Any, restored).config.model) is variants[1]


# Positions an adapter scope binds.


@model(tag="tool")
class ToolConfig(DiscriminatedModel):
    model: ModelConfig | None = None


@model(variant="search")
class SearchTool(ToolConfig):
    k: int = 3


class Shape[Kind: str](DiscriminatedModel):
    kind: Kind = tag_field()


class Circle(Shape[Literal["circle"]]):
    radius: float = 1.0


@model(tag="stage")
class Stage(DiscriminatedModel):
    pass


@model(variant="remote", tag="remote")
class RemoteStage(Stage):
    pass


@model(variant="cached")
class CachedRemote(RemoteStage):
    pass


class Wrapper(BaseModel):
    model: ModelConfig
    shown: SerializeAsAny[ModelConfig] | None = None


@model(tag="provider")
class FrozenModel(DiscriminatedModel):
    model_config = ConfigDict(frozen=True)
    model_id: str


@model(variant="openai")
class FrozenOpenAI(FrozenModel):
    reasoning_effort: str | None = None


@model(tag="holder")
class Holder(DiscriminatedModel):
    tools: list[ToolConfig] = []
    pair: tuple[ModelConfig, ToolConfig] | None = None
    optional: ModelConfig | None = None
    either: ModelConfig | ToolConfig | None = None
    mixed: ModelConfig | int = 0
    mixed_list: list[int | ModelConfig] = []
    named: dict[str, ModelConfig] = {}
    stage: RemoteStage | None = None
    shape: Shape[Any] | None = None
    wrapped: Wrapper | None = None
    frozen: set[FrozenModel] = set()
    frozen_items: frozenset[FrozenModel] = frozenset()
    children: list[Self] = []


@model(variant="full")
class FullHolder(Holder):
    pass


HOLDER_VARIANTS: list[type[DiscriminatedModel]] = [
    FullHolder,
    OpenAIModelConfig,
    AnthropicModelConfig,
    SearchTool,
    Circle,
    CachedRemote,
    FrozenOpenAI,
]


def holder_adapter() -> TypeAdapter[Holder]:
    return build_adapter(Holder, variants=HOLDER_VARIANTS)


def test_every_position_dispatches_to_the_concrete_variant() -> None:
    adapter = holder_adapter()
    value = adapter.validate_python(
        {
            "holder": "full",
            "tools": [{"tool": "search", "model": OPENAI}],
            "pair": [ANTHROPIC, {"tool": "search"}],
            "optional": OPENAI,
            "either": {"tool": "search", "k": 7},
            "mixed": OPENAI,
            "mixed_list": [1, ANTHROPIC],
            "named": {"x": OPENAI},
            "stage": {"stage": "remote", "remote": "cached"},
            "shape": {"kind": "circle", "radius": 2.0},
            "wrapped": {"model": OPENAI},
            "frozen": [{"provider": "openai", "model_id": "f", "reasoning_effort": "x"}],
            "frozen_items": [{"provider": "openai", "model_id": "g"}],
            "children": [{"holder": "full"}],
        }
    )
    assert isinstance(value, FullHolder)
    tool = value.tools[0]
    assert isinstance(tool, SearchTool) and isinstance(tool.model, OpenAIModelConfig)
    assert tool.model.reasoning_effort == "low"
    assert value.pair is not None
    assert [type(item) for item in value.pair] == [AnthropicModelConfig, SearchTool]
    assert type(value.optional) is OpenAIModelConfig
    assert type(value.either) is SearchTool
    assert type(value.mixed) is OpenAIModelConfig
    assert [type(item) for item in value.mixed_list] == [int, AnthropicModelConfig]
    assert type(value.named["x"]) is OpenAIModelConfig
    assert type(value.stage) is CachedRemote
    assert type(value.shape) is Circle
    assert value.wrapped is not None and type(value.wrapped) is Wrapper
    assert type(value.wrapped.model) is OpenAIModelConfig
    assert [type(item) for item in value.frozen] == [FrozenOpenAI]
    assert [type(item) for item in value.frozen_items] == [FrozenOpenAI]
    assert [type(child) for child in value.children] == [FullHolder]

    assert adapter.validate_json(adapter.dump_json(value, warnings="error")) == value
    # The plain Wrapper keeps native instance serialization (see the dump test).
    owned = value.model_copy(update={"wrapped": None})
    assert adapter.validate_python(owned.model_dump(mode="json", warnings="error")) == owned
    assert adapter.validate_python({"holder": "full", "mixed": 5}).mixed == 5
    either = adapter.validate_python({"holder": "full", "either": OPENAI}).either
    assert type(either) is OpenAIModelConfig


def test_union_rejects_unknown_tags_and_other_families() -> None:
    adapter = holder_adapter()
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        adapter.validate_python({"holder": "full", "optional": {"provider": "x", "model_id": "m"}})
    with pytest.raises(ValidationError):
        adapter.validate_python({"holder": "full", "stage": {"stage": "remote"}})


def test_instance_dumps_keep_variant_fields() -> None:
    adapter = holder_adapter()
    value = adapter.validate_python(
        {
            "holder": "full",
            "optional": OPENAI,
            "shape": {"kind": "circle", "radius": 2.0},
            "wrapped": {"model": OPENAI, "shown": OPENAI},
        }
    )
    dumped = value.model_dump(warnings="error")
    assert dumped["optional"]["reasoning_effort"] == "low"
    assert dumped["shape"] == {"kind": "circle", "radius": 2.0}
    # Ordinary models keep native serialization unless the field opts in.
    assert "reasoning_effort" not in dumped["wrapped"]["model"]
    assert dumped["wrapped"]["shown"]["reasoning_effort"] == "low"
    assert adapter.dump_python(value)["wrapped"]["model"]["reasoning_effort"] == "low"

    handmade = GenericConfig(model=OpenAIModelConfig(model_id="m", reasoning_effort="low"))
    assert handmade.model_dump()["model"]["reasoning_effort"] == "low"


def test_unused_variants_are_ignored() -> None:
    adapter = build_adapter(
        SessionConfig, variants=[GenericConfig, OpenAIModelConfig, SearchTool, Circle]
    )
    assert type(adapter.validate_python(RAW).model) is OpenAIModelConfig


@pytest.mark.parametrize("wide_first", [True, False])
def test_adapters_with_different_sets_coexist(wide_first: bool) -> None:
    def wide() -> TypeAdapter[SessionConfig]:
        return build_adapter(
            SessionConfig, variants=[GenericConfig, OpenAIModelConfig, AnthropicModelConfig]
        )

    def narrow() -> TypeAdapter[SessionConfig]:
        return build_adapter(SessionConfig, variants=[GenericConfig, AnthropicModelConfig])

    first, second = (wide(), narrow()) if wide_first else (narrow(), wide())
    wide_adapter, narrow_adapter = (first, second) if wide_first else (second, first)
    assert type(wide_adapter.validate_python(RAW).model) is OpenAIModelConfig
    with pytest.raises(ValidationError, match="expected tags: 'anthropic'"):
        narrow_adapter.validate_python(RAW)
    assert type(narrow_adapter.validate_python({**RAW, "model": ANTHROPIC}).model) is (
        AnthropicModelConfig
    )
    anthropic = {**RAW, "model": ANTHROPIC}
    assert wide_adapter.validate_python(anthropic) == narrow_adapter.validate_python(anthropic)


def test_json_schema_lists_exactly_the_scope_variants() -> None:
    def mapping(schema: dict[str, Any], owner: str) -> list[str]:
        prop = schema["$defs"][owner]["properties"]["model"]
        if "$ref" in prop:
            prop = schema["$defs"][prop["$ref"].rsplit("/", 1)[1]]
        return sorted(prop["discriminator"]["mapping"])

    narrow = build_adapter(SessionConfig, variants=[GenericConfig, AnthropicModelConfig])
    for mode in ("validation", "serialization"):
        assert mapping(narrow.json_schema(mode=mode), "GenericConfig") == ["anthropic"]

    class Envelope(BaseModel):
        config: Annotated[SessionConfig, bind_variants([GenericConfig, OpenAIModelConfig])]
        eager: Annotated[ModelConfig, bind_variants([AnthropicModelConfig])] | None = None

    rebuild_bindings(Envelope)
    for mode in ("validation", "serialization"):
        schema = Envelope.model_json_schema(mode=mode)
        assert mapping(schema, "GenericConfig") == ["openai"]
        eager = json.dumps(schema["properties"]["eager"])
        assert "AnthropicModelConfig" in eager and "OpenAIModelConfig" not in eager


def test_errors_are_titled_after_the_root_family() -> None:
    adapter = build_adapter(SessionConfig, variants=[GenericConfig, OpenAIModelConfig])
    with pytest.raises(ValidationError) as error:
        adapter.validate_python({**RAW, "model": {"provider": "zzz"}})
    assert error.value.title == "SessionConfig"


def test_scopes_leave_classes_and_direct_validation_unchanged() -> None:
    validator = GenericConfig.__pydantic_validator__
    annotation = GenericConfig.model_fields["model"].annotation
    build_adapter(SessionConfig, variants=[GenericConfig, OpenAIModelConfig])
    assert GenericConfig.__pydantic_validator__ is validator
    assert GenericConfig.model_fields["model"].annotation is annotation
    assert type(GenericConfig.model_validate(RAW).model) is ModelConfig


def test_existing_instances_keep_native_revalidation() -> None:
    wide = build_adapter(
        SessionConfig, variants=[GenericConfig, OpenAIModelConfig, AnthropicModelConfig]
    )
    narrow = build_adapter(SessionConfig, variants=[GenericConfig, OpenAIModelConfig])
    value = wide.validate_python({**RAW, "model": ANTHROPIC})
    assert narrow.validate_python(value) is value


# Errors.


@model(tag="left")
class Left(DiscriminatedModel):
    right: "Right | None" = None


@model(tag="right")
class Right(DiscriminatedModel):
    left: Left | None = None


@model(variant="l")
class LeftLeaf(Left):
    pass


@model(variant="r")
class RightLeaf(Right):
    pass


class Tree[Kind: str](DiscriminatedModel):
    kind: Kind = tag_field()
    kids: "list[TreeFamily]" = []


TreeFamily = Tree[Any]


class Branch(Tree[Literal["branch"]]):
    pass


for _cyclic in (Left, Right, LeftLeaf, RightLeaf, Branch):
    _cyclic.model_rebuild(force=True)


@model(tag="init")
class CustomInit(DiscriminatedModel):
    model: ModelConfig


@model(variant="custom")
class CustomInitLeaf(CustomInit):
    def __init__(self, **data: Any) -> None:
        super().__init__(**data)


def test_reached_family_without_variants_names_the_field() -> None:
    with pytest.raises(
        ValueError, match=r"No variants supplied for .*ModelConfig.*SessionConfig\.model"
    ):
        build_adapter(SessionConfig, variants=[GenericConfig])
    with pytest.raises(ValueError, match="No variants supplied for .*SessionConfig"):
        build_adapter(SessionConfig, variants=[OpenAIModelConfig])


def test_cycles_through_automatic_fields_are_rejected() -> None:
    with pytest.raises(TypeError, match=r"Left\.right -> Right\.left .*bind_variants"):
        build_adapter(Left, variants=[LeftLeaf, RightLeaf])
    with pytest.raises(TypeError, match=r"Tree\.kids .*bind_variants"):
        build_adapter(TreeFamily, variants=[Branch])


def test_custom_initializers_on_rebound_paths_are_rejected() -> None:
    with pytest.raises(TypeError, match=r"CustomInitLeaf defines a custom __init__"):
        build_adapter(CustomInit, variants=[CustomInitLeaf, OpenAIModelConfig])


# bind_variants lifecycle.


def test_bindings_without_nested_families_stay_eager() -> None:
    adapter = TypeAdapter[ModelConfig](
        Annotated[ModelConfig, bind_variants([OpenAIModelConfig, GenericConfig])]
    )
    assert type(adapter.validate_python(OPENAI)) is OpenAIModelConfig


def test_standalone_adapter_with_nested_families_raises() -> None:
    adapter = TypeAdapter[SessionConfig](
        Annotated[SessionConfig, bind_variants([GenericConfig, OpenAIModelConfig])]
    )
    with pytest.raises(ValidationError) as error:
        adapter.validate_python(RAW)
    assert_unassembled(error.value)


def test_binding_lifecycle_across_models_and_adapters() -> None:
    binding = bind_variants([GenericConfig, OpenAIModelConfig])

    class Envelope(BaseModel):
        config: Annotated[SessionConfig, binding]

    class EarlyOuter(BaseModel):
        envelope: Envelope

    stale = TypeAdapter(Envelope)
    rebuild_bindings(Envelope)

    class LateOuter(BaseModel):
        envelope: Envelope

    class Reused(BaseModel):
        config: Annotated[SessionConfig, binding]

    assert type(Envelope.model_validate({"config": RAW}).config.model) is OpenAIModelConfig
    late = LateOuter.model_validate({"envelope": {"config": RAW}})
    assert type(late.envelope.config.model) is OpenAIModelConfig
    for failing in (
        lambda: EarlyOuter.model_validate({"envelope": {"config": RAW}}),
        lambda: Reused.model_validate({"config": RAW}),
        lambda: stale.validate_python({"config": RAW}),
    ):
        with pytest.raises(ValidationError) as error:
            failing()
        assert_unassembled(error.value)

    rebuild_bindings(EarlyOuter, Reused)
    stale.rebuild(force=True)
    assert type(EarlyOuter.model_validate({"envelope": {"config": RAW}}).envelope.config.model) is (
        OpenAIModelConfig
    )
    assert type(Reused.model_validate({"config": RAW}).config.model) is OpenAIModelConfig
    assert type(stale.validate_python({"config": RAW}).config.model) is OpenAIModelConfig

    Envelope.model_rebuild(force=True)
    with pytest.raises(ValidationError) as error:
        Envelope.model_validate({"config": RAW})
    assert_unassembled(error.value)


def test_failed_rebuild_bindings_restores_the_failing_state() -> None:
    class Parent(BaseModel):
        name: str = "parent"

    class Envelope(Parent):
        config: Annotated[SessionConfig, bind_variants([GenericConfig])]

    with pytest.raises(ValueError, match=r"No variants supplied for .*ModelConfig"):
        rebuild_bindings(Envelope)
    with pytest.raises(ValidationError) as error:
        Envelope.model_validate({"config": RAW})
    assert_unassembled(error.value)


def test_binding_decides_eagerness_per_root() -> None:
    shared = bind_variants([OpenAIModelConfig, GenericConfig])

    class Both(BaseModel):
        model: Annotated[ModelConfig, shared]
        session: Annotated[SessionConfig, shared] | None = None

    assert type(Both.model_validate({"model": OPENAI}).model) is OpenAIModelConfig
    with pytest.raises(ValidationError) as error:
        Both.model_validate({"model": OPENAI, "session": RAW})
    assert_unassembled(error.value)
    rebuild_bindings(Both)
    both = Both.model_validate({"model": OPENAI, "session": RAW})
    assert both.session is not None and type(both.session.model) is OpenAIModelConfig


@model(tag="job")
class Job(DiscriminatedModel):
    primary: ModelConfig


@model(variant="run")
class RunJob(Job):
    pinned: Annotated[SessionConfig, bind_variants([GenericConfig, OpenAIModelConfig])]


def test_closed_scope_inside_an_adapter_uses_its_own_variants() -> None:
    adapter = build_adapter(Job, variants=[RunJob, AnthropicModelConfig])
    value = adapter.validate_python({"job": "run", "primary": ANTHROPIC, "pinned": RAW})
    assert isinstance(value, RunJob)
    assert type(value.primary) is AnthropicModelConfig
    assert type(value.pinned.model) is OpenAIModelConfig
    with pytest.raises(ValidationError, match="expected tags: 'openai'"):
        adapter.validate_python(
            {"job": "run", "primary": ANTHROPIC, "pinned": {**RAW, "model": ANTHROPIC}}
        )


# Regressions from implementation review.


@model(tag="pending")
class Pending(DiscriminatedModel):
    pass


@model(variant="p")
class PendingVariant(Pending):
    pass


# A leaf whose field refers to a family that its module defines later.
PendingLeaf = create_model(
    "PendingLeaf",
    __base__=PendingVariant,
    __module__=__name__,
    provider=(cast(Any, "LateProvider | None"), None),
)


def test_scopes_built_before_forward_references_resolve_are_reevaluated() -> None:
    early = build_adapter(Pending, variants=[PendingLeaf])
    with pytest.raises(PydanticUserError, match="not fully defined"):
        early.validate_python({"pending": "p"})
    globals()["LateProvider"] = ModelConfig
    PendingLeaf.model_rebuild(force=True)
    late = build_adapter(Pending, variants=[PendingLeaf, OpenAIModelConfig])
    value = late.validate_python({"pending": "p", "provider": OPENAI})
    assert type(cast(Any, value).provider) is OpenAIModelConfig
    with pytest.raises(ValueError, match=r"No variants supplied for .*ModelConfig"):
        build_adapter(Pending, variants=[PendingLeaf])


def test_concurrent_scope_builds_are_independent() -> None:
    results: list[type] = []

    def build() -> None:
        adapter = build_adapter(SessionConfig, variants=[GenericConfig, OpenAIModelConfig])
        results.append(type(adapter.validate_python(RAW).model))

    threads = [threading.Thread(target=build) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results == [OpenAIModelConfig] * 8


@model(tag="labelled")
class Labelled(DiscriminatedModel):
    value: Annotated[GenericConfig, Tag("session")] | Annotated[int, Tag("number")] = 0


@model(variant="l")
class LabelledLeaf(Labelled):
    pass


def test_labelled_union_choices_keep_identity_and_fields() -> None:
    from pydantic import Discriminator

    def choose(value: object) -> str:
        return "number" if isinstance(value, int) else "session"

    class Box(BaseModel):
        item: Annotated[
            Annotated[GenericConfig, Tag("session")] | Annotated[int, Tag("number")],
            Discriminator(choose),
        ]

    @model(tag="boxed")
    class Boxed(DiscriminatedModel):
        box: Box

    @model(variant="b")
    class BoxedLeaf(Boxed):
        pass

    adapter = build_adapter(Boxed, variants=[BoxedLeaf, OpenAIModelConfig])
    value = adapter.validate_python({"boxed": "b", "box": {"item": {"model": OPENAI}}})
    item = value.box.item
    assert type(item) is GenericConfig and type(item.model) is OpenAIModelConfig
    assert item == GenericConfig(model=OpenAIModelConfig(model_id="m", reasoning_effort="low"))
    assert adapter.validate_json(adapter.dump_json(value, warnings="error")) == value


def test_validators_and_declaration_hooks_see_the_declared_classes() -> None:
    seen: list[type] = []
    registered: list[type] = []

    @model(tag="hooked")
    class Hooked(DiscriminatedModel):
        model: ModelConfig

        @field_validator("model")
        @classmethod
        def record(cls, value: ModelConfig) -> ModelConfig:
            seen.append(cls)
            return value

        @classmethod
        def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
            super().__pydantic_init_subclass__(**kwargs)
            registered.append(cls)

    @model(variant="h")
    class HookedLeaf(Hooked):
        pass

    before = len(registered)
    adapter = build_adapter(Hooked, variants=[HookedLeaf, OpenAIModelConfig])
    assert len(registered) == before
    assert HookedLeaf.__subclasses__() == []
    adapter.validate_python({"hooked": "h", "model": OPENAI})
    assert seen == [HookedLeaf]


@model(tag="recursive")
class Recursive(DiscriminatedModel):
    setting: ModelConfig | None = None


class TreeNode(BaseModel):
    child: "TreeNode | None" = None
    setting: ModelConfig | None = None


@model(variant="concrete")
class ConcreteRecursive(Recursive):
    children: "list[ConcreteRecursive]" = []
    tree: TreeNode | None = None


ConcreteRecursive.model_rebuild(force=True)


def test_concrete_and_plain_model_recursion_is_not_a_family_cycle() -> None:
    adapter = build_adapter(Recursive, variants=[ConcreteRecursive, OpenAIModelConfig])
    value = adapter.validate_python(
        {
            "recursive": "concrete",
            "children": [{"recursive": "concrete", "setting": OPENAI}],
            "tree": {"child": {"setting": OPENAI}},
        }
    )
    assert isinstance(value, ConcreteRecursive)
    assert type(value.children[0].setting) is OpenAIModelConfig
    assert value.tree is not None and value.tree.child is not None
    assert type(value.tree.child.setting) is OpenAIModelConfig


@model(tag="node")
class DecoratedNode(DiscriminatedModel):
    children: "list[DecoratedNode]" = []


@model(variant="leaf")
class DecoratedLeaf(DecoratedNode):
    pass


def test_decorated_class_name_self_reference_is_a_cycle() -> None:
    with pytest.raises(TypeError, match=r"DecoratedNode\.children .*bind_variants"):
        build_adapter(DecoratedNode, variants=[DecoratedLeaf])


class SharedNode(BaseModel):
    child: "SharedNode | None" = None
    provider: ModelConfig | None = None


class CustomInitHolder(BaseModel):
    node: SharedNode

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)


@model(tag="shared")
class SharedRoot(DiscriminatedModel):
    pass


@model(variant="first")
class SharedFirst(SharedRoot):
    first: SharedNode
    second: CustomInitHolder


@model(variant="second")
class SharedSecond(SharedRoot):
    second: CustomInitHolder
    first: SharedNode


def test_custom_initializers_behind_shared_definitions_are_rejected() -> None:
    for leaf in (SharedFirst, SharedSecond):
        with pytest.raises(TypeError, match="CustomInitHolder defines a custom __init__"):
            build_adapter(SharedRoot, variants=[leaf, OpenAIModelConfig])


@model(tag="defaults")
class Defaults(DiscriminatedModel):
    provider: ModelConfig
    options: dict[str, str] = {"ref": "application-key"}
    shaped: dict[str, str] = {"type": "model", "schema_ref": "x"}


@model(variant="d")
class DefaultsLeaf(Defaults):
    pass


def test_default_values_are_not_treated_as_schemas() -> None:
    adapter = build_adapter(Defaults, variants=[DefaultsLeaf, OpenAIModelConfig])
    value = adapter.validate_python({"defaults": "d", "provider": OPENAI})
    assert value.options == {"ref": "application-key"}
    assert value.shaped == {"type": "model", "schema_ref": "x"}


@model(tag="provider")
class Counted(DiscriminatedModel):
    value: int = 0

    @model_validator(mode="after")
    def add_one(self) -> Self:
        self.value += 1
        return self

    @model_validator(mode="after")
    def add_ten(self) -> Self:
        self.value += 10
        return self


@model(variant="c")
class CountedLeaf(Counted):
    pass


@model(tag="counter")
class Counter(DiscriminatedModel):
    counted: Counted


@model(variant="k")
class CounterLeaf(Counter):
    pass


def test_family_model_validators_run_once() -> None:
    adapter = build_adapter(Counter, variants=[CounterLeaf, CountedLeaf])
    value = adapter.validate_python({"counter": "k", "counted": {"provider": "c"}})
    assert type(value.counted) is CountedLeaf
    assert value.counted.value == CountedLeaf.model_validate({}).value == 11


@model(tag="kind")
class NamedRoot(DiscriminatedModel):
    pass


@model(variant="NamedRoot")
class NamedRootLeaf(NamedRoot):
    value: int = 0


def test_literal_tags_are_not_type_references() -> None:
    adapter = TypeAdapter[NamedRoot](Annotated[NamedRoot, bind_variants([NamedRootLeaf])])
    assert type(adapter.validate_python({"kind": "NamedRoot", "value": 1})) is NamedRootLeaf


@model(tag="expected")
class Configured(DiscriminatedModel):
    config: ModelConfig
    default: ModelConfig | None = None


@model(variant="default")
class ConfiguredLeaf(Configured):
    pass


def test_field_names_and_tags_matching_schema_keys_are_bound() -> None:
    adapter = build_adapter(Configured, variants=[ConfiguredLeaf, OpenAIModelConfig])
    value = adapter.validate_python({"expected": "default", "config": OPENAI, "default": OPENAI})
    assert type(value.config) is OpenAIModelConfig
    assert type(value.default) is OpenAIModelConfig


type AdjustedModel = Annotated[ModelConfig, AfterValidator(lambda value: _require_effort(value))]


def _require_effort(value: ModelConfig) -> ModelConfig:
    if getattr(value, "reasoning_effort", None) != "low":
        raise ValueError("effort must be low")
    return value


@model(tag="aliased")
class Aliased(DiscriminatedModel):
    model: AdjustedModel


@model(variant="a")
class AliasedLeaf(Aliased):
    pass


def test_alias_validators_on_family_fields_still_run() -> None:
    adapter = build_adapter(Aliased, variants=[AliasedLeaf, OpenAIModelConfig])
    assert type(adapter.validate_python({"aliased": "a", "model": OPENAI}).model) is (
        OpenAIModelConfig
    )
    with pytest.raises(ValidationError, match="effort must be low"):
        adapter.validate_python({"aliased": "a", "model": {**OPENAI, "reasoning_effort": "x"}})


class Unreachable(BaseModel):
    child: "Unreachable | None" = None
    deep: ToolConfig | None = None

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)


@model(tag="loose")
class Loose(DiscriminatedModel):
    value: Unreachable | int = 0


@model(variant="narrow")
class NarrowLooseBase(Loose):
    pass


# The variant narrows the family's broad field, so Unreachable is never constructed.
NarrowLoose = create_model("NarrowLoose", __base__=NarrowLooseBase, value=(int, 0))


@model(tag="outer")
class OuterLoose(DiscriminatedModel):
    loose: Loose


@model(variant="o")
class OuterLooseLeaf(OuterLoose):
    pass


def test_custom_initializers_removed_by_narrowing_are_not_rejected() -> None:
    adapter = build_adapter(OuterLoose, variants=[OuterLooseLeaf, NarrowLoose])
    value = adapter.validate_python({"outer": "o", "loose": {"loose": "narrow", "value": 7}})
    assert type(value.loose) is NarrowLoose


class SharedProvider(BaseModel):
    provider: ModelConfig


class ChainedCustomInit(BaseModel):
    child: "ChainedCustomInit | None" = None
    first: SharedProvider
    second: SharedProvider

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)


@model(tag="chained")
class Chained(DiscriminatedModel):
    custom: ChainedCustomInit


@model(variant="c")
class ChainedLeaf(Chained):
    pass


def test_custom_initializers_reaching_families_through_shared_models_are_rejected() -> None:
    with pytest.raises(TypeError, match="ChainedCustomInit defines a custom __init__"):
        build_adapter(Chained, variants=[ChainedLeaf, OpenAIModelConfig])


@model(tag="provider")
class DeferredCounted(DiscriminatedModel):
    model_config = ConfigDict(defer_build=True)
    value: int = 0

    @model_validator(mode="after")
    def add_one(self) -> Self:
        self.value += 1
        return self

    @model_validator(mode="after")
    def add_ten(self) -> Self:
        self.value += 10
        return self


@model(variant="c")
class DeferredCountedLeaf(DeferredCounted):
    pass


@model(tag="counter")
class DeferredCounter(DiscriminatedModel):
    counted: DeferredCounted


@model(variant="k")
class DeferredCounterLeaf(DeferredCounter):
    pass


def test_deferred_family_validators_run_once() -> None:
    adapter = build_adapter(DeferredCounter, variants=[DeferredCounterLeaf, DeferredCountedLeaf])
    value = adapter.validate_python({"counter": "k", "counted": {"provider": "c"}})
    assert type(value.counted) is DeferredCountedLeaf
    assert value.counted.value == 11


@model(tag="setting")
class Setting(DiscriminatedModel):
    pass


@model(tag="deep")
class Deep(DiscriminatedModel):
    setting: Setting


@model(variant="d")
class DeepLeaf(Deep):
    pass


@model(tag="provider")
class DeferredNarrowed(DiscriminatedModel):
    model_config = ConfigDict(defer_build=True)
    inner: Annotated[Deep, bind_variants([DeepLeaf])] | int = 0


@model(variant="p")
class DeferredNarrowedBase(DeferredNarrowed):
    pass


# The supplied variant narrows away the base family's closed binding.
DeferredNarrowedLeaf = create_model(
    "DeferredNarrowedLeaf", __base__=DeferredNarrowedBase, inner=(int, 0), extra=(int, 0)
)


@model(tag="root")
class NarrowedRoot(DiscriminatedModel):
    provider: DeferredNarrowed


@model(variant="r")
class NarrowedRootLeaf(NarrowedRoot):
    pass


def test_deferred_family_inspection_does_not_assemble_discarded_bindings() -> None:
    adapter = build_adapter(NarrowedRoot, variants=[NarrowedRootLeaf, DeferredNarrowedLeaf])
    value = adapter.validate_python(
        {"root": "r", "provider": {"provider": "p", "inner": 5, "extra": 7}}
    )
    assert type(value.provider) is DeferredNarrowedLeaf
    assert value.provider.model_dump() == {"inner": 5, "provider": "p", "extra": 7}

    class Container(BaseModel):
        root: Annotated[NarrowedRoot, bind_variants([NarrowedRootLeaf, DeferredNarrowedLeaf])]

    rebuild_bindings(Container)
    nested = Container.model_validate(
        {"root": {"root": "r", "provider": {"provider": "p", "inner": 5, "extra": 7}}}
    )
    assert type(nested.root.provider) is DeferredNarrowedLeaf


class IdentityShape[Kind: str](IdentityDiscriminatedModel):
    kind: Kind = tag_field()


class IdentitySquare(IdentityShape[Literal["square"]]):
    side: float = 1.0


class IdentityCanvas[Kind: str](IdentityDiscriminatedModel):
    canvas: Kind = tag_field()
    shape: IdentityShape[Any]


class IdentityPaper(IdentityCanvas[Literal["paper"]]):
    pass


def test_explicit_generic_identity_families_bind_and_round_trip() -> None:
    adapter = build_adapter(IdentityCanvas[Any], variants=[IdentityPaper, IdentitySquare])
    value = adapter.validate_python({"canvas": "paper", "shape": {"kind": "square", "side": 2}})
    assert type(value) is IdentityPaper and type(value.shape) is IdentitySquare
    dumped = value.model_dump(warnings="error")
    assert dumped == {"canvas": "paper", "shape": {"kind": "square", "side": 2.0}}
    assert adapter.validate_python(dumped) == value
    assert adapter.validate_json(adapter.dump_json(value, warnings="error")) == value


def test_rebuild_bindings_order_does_not_matter() -> None:
    class OuterFirst(BaseModel):
        job: RunJob

    class OuterLast(BaseModel):
        job: RunJob

    data = {"job": {"job": "run", "primary": ANTHROPIC, "pinned": RAW}}
    for outer, order in ((OuterFirst, "outer-first"), (OuterLast, "outer-last")):
        models: list[type[BaseModel]] = (
            [outer, RunJob] if order == "outer-first" else [RunJob, outer]
        )
        RunJob.model_rebuild(force=True)
        outer.model_rebuild(force=True)
        rebuild_bindings(*models)
        value = outer.model_validate(data)
        assert type(value.job.pinned.model) is OpenAIModelConfig


@model(tag="later")
class LaterHolder(DiscriminatedModel):
    provider: "LaterFamily"


@model(variant="h")
class LaterHolderLeaf(LaterHolder):
    pass


@model(tag="provider")
class LaterFamily(DiscriminatedModel):
    model_id: str


@model(variant="openai")
class LaterOpenAI(LaterFamily):
    reasoning_effort: str | None = None


def test_string_annotations_resolved_after_class_creation_are_bound() -> None:
    # The leaf is not rebuilt first: the scope evaluates its string annotation.
    adapter = build_adapter(LaterHolder, variants=[LaterHolderLeaf, LaterOpenAI])
    value = adapter.validate_python({"later": "h", "provider": OPENAI})
    assert type(value.provider) is LaterOpenAI


def test_forced_rebuild_of_an_assembled_inherited_container_fails_loudly() -> None:
    class Parent(BaseModel):
        name: str = "parent"

    class Envelope(Parent):
        config: Annotated[SessionConfig, bind_variants([GenericConfig, OpenAIModelConfig])]

    rebuild_bindings(Envelope)
    assert type(Envelope.model_validate({"config": RAW}).config.model) is OpenAIModelConfig
    Envelope.model_rebuild(force=True)
    with pytest.raises(ValidationError) as error:
        Envelope.model_validate({"config": RAW})
    assert_unassembled(error.value)


def test_python_mode_instance_round_trip() -> None:
    adapter = holder_adapter()
    value = adapter.validate_python(
        {
            "holder": "full",
            "tools": [{"tool": "search", "model": OPENAI}],
            "pair": [ANTHROPIC, {"tool": "search"}],
            "optional": OPENAI,
            "mixed": OPENAI,
            "named": {"x": OPENAI},
            "shape": {"kind": "circle", "radius": 2.0},
        }
    )
    assert adapter.validate_python(value.model_dump(warnings="error")) == value


def test_mixed_union_rejects_unknown_tags() -> None:
    adapter = holder_adapter()
    with pytest.raises(ValidationError, match="does not match any of the expected tags"):
        adapter.validate_python({"holder": "full", "mixed": {"provider": "x", "model_id": "m"}})


class CycleA(BaseModel):
    value: Annotated[SessionConfig, bind_variants([GenericConfig, OpenAIModelConfig])]
    other: "CycleB | None" = None


class CycleB(BaseModel):
    other: CycleA | None = None


CycleA.model_rebuild()


@pytest.mark.parametrize("order", ["a-first", "b-first"])
def test_rebuild_bindings_assembles_mutually_recursive_containers(order: str) -> None:
    CycleA.model_rebuild(force=True)
    CycleB.model_rebuild(force=True)
    models: list[type[BaseModel]] = [CycleA, CycleB] if order == "a-first" else [CycleB, CycleA]
    rebuild_bindings(*models)
    value = CycleB.model_validate({"other": {"value": RAW, "other": {"other": {"value": RAW}}}})
    assert value.other is not None
    assert type(value.other.value.model) is OpenAIModelConfig
    CycleB.model_json_schema()


def test_rebuild_bindings_reports_containers_it_cannot_assemble() -> None:
    class Shared(BaseModel):
        value: Annotated[SessionConfig, bind_variants([GenericConfig, OpenAIModelConfig])]

    class PeerA(BaseModel):
        peer: "PeerB | None" = None
        child: Shared | None = None

    class PeerB(BaseModel):
        peer: PeerA | None = None
        child: Shared | None = None

    PeerA.model_rebuild(_types_namespace={"PeerB": PeerB})
    with pytest.raises(TypeError, match=r"could not assemble .*PeerA.*PeerB"):
        rebuild_bindings(Shared, PeerA, PeerB)
    with pytest.raises(ValidationError) as error:
        PeerA.model_validate({"child": {"value": RAW}})
    assert_unassembled(error.value)

    class ReadyShared(BaseModel):
        value: Annotated[SessionConfig, bind_variants([GenericConfig, OpenAIModelConfig])]

    rebuild_bindings(ReadyShared)

    class ReadyA(BaseModel):
        peer: "ReadyB | None" = None
        child: ReadyShared | None = None

    class ReadyB(BaseModel):
        peer: ReadyA | None = None
        child: ReadyShared | None = None

    ReadyA.model_rebuild(_types_namespace={"ReadyB": ReadyB})
    rebuild_bindings(ReadyA, ReadyB)
    value = ReadyA.model_validate({"peer": {"child": {"value": RAW}}})
    assert value.peer is not None and value.peer.child is not None
    assert type(value.peer.child.value.model) is OpenAIModelConfig
