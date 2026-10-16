"""Exercise decorator declarations through the existing model and behavior pipelines."""

from abc import abstractmethod
from importlib import metadata
from typing import Annotated, Any, Literal, Protocol, Self, assert_type, cast

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from typebind import (
    DiscriminatedModel,
    IdentityDiscriminatedModel,
    Implementation,
    assemble,
    bind_variants,
    build_adapter,
    discover,
    model,
    select_implementations,
    select_variants,
    tag_field,
)


@model(tag="kind")
class Message(IdentityDiscriminatedModel):
    identifier: str

    def describe(self) -> str:
        return self.identifier


@model(tag="method", variant="request")
class Request(Message):
    sequence: int = 0

    def describe(self) -> str:
        return f"{super().describe()}:{self.sequence}"


@model(variant="query")
class Query(Request):
    text: str

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def observe_tags(self) -> Self:
        assert {"kind", "method"} <= self.model_fields_set
        return self


@model(variant="write")
class Write(Request):
    value: int


@model(tag="mode", variant="batch")
class Batch(Request):
    batch_id: str


@model(variant="stream")
class Stream(Batch):
    cursor: str


binding = bind_variants([Query, Write, Stream])
type MessageValue = Annotated[Message, binding]


class Envelope(BaseModel):
    primary: MessageValue | None = None
    requests: list[Annotated[Request, binding]]
    by_name: dict[str, MessageValue]


class Describable(Protocol):
    @property
    @abstractmethod
    def model(self) -> Request: ...

    @abstractmethod
    def describe(self) -> str: ...


class QueryObject(Implementation[Query], Describable):
    def describe(self) -> str:
        return self.model.describe()


def test_payload_and_multilevel_roundtrip() -> None:
    query = Query(identifier="q", text=" hello ")
    assert_type(query.text, str)
    assert_type(query.sequence, int)
    assert query.describe() == "q:0"
    assert query.model_dump(exclude_unset=True) == {
        "identifier": "q",
        "kind": "request",
        "method": "query",
        "text": "hello",
    }
    stream = Stream(identifier="s", batch_id="b", cursor="c")
    envelope = Envelope(primary=query, requests=[query, stream], by_name={"q": query})
    restored = Envelope.model_validate_json(envelope.model_dump_json(exclude_unset=True))
    assert restored == envelope
    assert type(restored.primary) is Query
    assert type(restored.requests[1]) is Stream
    assert type(restored.by_name["q"]) is Query
    for root in (Message, Request):
        adapter = build_adapter(root, variants=[Query, Write, Stream])
        assert type(adapter.validate_json(adapter.dump_json(stream))) is Stream
        assert adapter.json_schema()["discriminator"]["propertyName"] in ("kind", "method")
    assert Query.model_fields["method"].annotation == Literal["query"]
    assert Query.model_fields["kind"].annotation == Literal["request"]
    assert "method" not in query.model_dump(exclude_defaults=True)
    with pytest.raises(ValidationError, match="frozen"):
        setattr(query, "method", "write")  # noqa: B010 - runtime-only field
    with pytest.raises(ValidationError, match="literal_error"):
        Query.model_validate({"identifier": "q", "text": "hi", "method": "write"})


def test_basic_policy_has_native_schema_without_marking() -> None:
    @model(tag="kind")
    class Plain(DiscriminatedModel):
        value: int

    @model(variant="event")
    class Event(Plain):
        pass

    assert Event.__pydantic_core_schema__["type"] == "model"
    assert Event(value=1).model_fields_set == {"value"}
    assert Event(value=1).model_dump(exclude_unset=True) == {"value": 1}
    assert Event(value=1).model_dump() == {"kind": "event", "value": 1}
    with pytest.raises(ValueError, match="bound discriminator"):
        Event(value=1).model_copy(update={"kind": "event"})
    with pytest.raises(ValidationError, match="Field required"):
        Plain(value=1)


def test_duplicate_leaves_and_intermediate_families() -> None:
    @model(variant="query")
    class OtherQuery(Request):
        pass

    with pytest.raises(
        ValueError, match=r"Duplicate tag 'query'.*Request.method.*Query.*OtherQuery"
    ):
        build_adapter(Message, variants=[Query, OtherQuery])

    @model(tag="operation", variant="request")
    class OtherRequest(Message):
        pass

    @model(variant="query")
    class OtherLeaf(OtherRequest):
        pass

    with pytest.raises(ValueError, match=r"Duplicate tag 'request'.*Message.kind"):
        bind_variants([Query, OtherLeaf])
    build_adapter(Message, variants=[Query, Query])
    # Reusing a tag under an independent family is valid.
    build_adapter(OtherRequest, variants=[OtherLeaf])


def test_pending_subclasses_cannot_be_assembled_as_leaves() -> None:
    class Pending(Request):
        text: str

    with pytest.raises(TypeError, match="tagged leaf"):
        build_adapter(Message, variants=[Pending])
    with pytest.raises(TypeError, match="concrete model"):
        assemble(variants=[Pending], implementations=[])


@pytest.mark.parametrize("name", ["", "_private", 1])
def test_invalid_discriminator_names(name: Any) -> None:
    with pytest.raises(ValueError, match="non-empty field name"):
        model(tag=name)


@pytest.mark.parametrize("tag", ["", 42])
def test_invalid_variant_values(tag: Any) -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        model(variant=tag)


def test_declaration_diagnostics() -> None:
    with pytest.raises(TypeError, match="DiscriminatedModel"):
        model(tag="kind")(cast(Any, object))
    with pytest.raises(TypeError, match="open family"):
        model(variant="again")(Query)
    with pytest.raises(TypeError, match="bind.*before introducing"):
        model(tag="next")(Request)
    for name in ("identifier", "describe", "model_dump", "kind"):
        with pytest.raises(TypeError, match="conflicts"):
            model(tag=name)(Query)
    with pytest.raises(TypeError, match="cannot be overridden"):

        class _Retag(Query):
            method: Literal["wrong"] = tag_field()

        _ = _Retag


def test_explicit_generics_coexist() -> None:
    class Explicit[Kind: str](DiscriminatedModel):
        kind: Kind = tag_field()
        identifier: str

    with pytest.raises(TypeError, match="specialize explicit generic"):
        model(tag="method")(Explicit)

    @model(tag="method")
    class Mixed(Explicit[Literal["request"]]):
        sequence: int

    @model(variant="query")
    class MixedQuery(Mixed):
        text: str

    adapter = build_adapter(Explicit[Any], variants=[MixedQuery])
    value = MixedQuery(identifier="q", sequence=1, text="hi")
    assert type(adapter.validate_json(adapter.dump_json(value))) is MixedQuery

    class GenericChild[Mode: str](Query):
        mode: Mode = tag_field()

    class Leaf(GenericChild[Literal["extra"]]):
        pass

    assert build_adapter(Message, variants=[Leaf]).validate_python(
        {"kind": "request", "method": "query", "mode": "extra", "identifier": "q", "text": "hi"}
    ) == Leaf(identifier="q", text="hi")


def test_deferred_schema_and_recursive_self() -> None:
    @model(tag="kind")
    class Tree(DiscriminatedModel):
        model_config = ConfigDict(defer_build=True, extra="forbid")
        name: str

    @model(variant="node")
    class Node(Tree):
        children: list[Self] = []

    value = Node(name="root", children=[Node(name="child")])
    assert Node.model_rebuild(force=True)
    adapter = build_adapter(Tree, variants=[Node])
    restored = adapter.validate_json(adapter.dump_json(value))
    assert isinstance(restored, Node)
    assert type(restored.children[0]) is Node
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Node.model_validate({"name": "x", "unknown": 1})


def test_discovery_scope_and_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    entries = metadata.EntryPoints(
        [
            metadata.EntryPoint(name="query", value=f"{__name__}:Query", group="test.models"),
            metadata.EntryPoint(name="write", value=f"{__name__}:Write", group="test.models"),
            metadata.EntryPoint(
                name="query", value=f"{__name__}:QueryObject", group="test.behaviors"
            ),
        ]
    )
    monkeypatch.setattr(metadata, "entry_points", lambda: entries)
    snapshot = discover(
        groups=["test.models", "test.behaviors"], include=lambda entry: entry.name == "query"
    )
    selected = select_variants(snapshot, family=Request, groups=["test.models"])
    implementations = select_implementations(snapshot, groups=["test.behaviors"])
    assert selected == (Query,)
    runtime = assemble(
        variants=selected, implementations=implementations, required_interfaces=[Describable]
    )
    value = Query(identifier="q", text="hello")
    view = runtime.adapt(value, Describable)
    assert_type(view, Describable)
    assert view.model is value
    assert view.describe() == "q:0"


def test_no_declaration_work_in_hot_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    import typebind._models as models

    adapter = build_adapter(Message, variants=[Query])
    runtime = assemble(
        variants=[Query], implementations=[QueryObject], required_interfaces=[Describable]
    )

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("declaration work at runtime")

    for name in (
        "_tag_fields",
        "_literal_tag",
        "_open_discriminator",
        "_bound_names",
        "create_model",
    ):
        monkeypatch.setattr(models, name, forbidden)
    value = Query(identifier="q", text="hi").model_copy(update={"text": "copy"})
    assert adapter.validate_json(adapter.dump_json(value, exclude_unset=True)) == value
    assert runtime.adapt(value, Describable).describe() == "q:0"


def test_combined_and_stacked_forms_match() -> None:
    @model(tag="kind")
    class Root(DiscriminatedModel):
        identifier: str

    @model(tag="method")
    @model(variant="request")
    class Stacked(Root):
        count: int = 1

    @model(tag="method", variant="request")
    class Combined(Root):
        count: int = 1

    @model(variant="query")
    class StackedLeaf(Stacked):
        pass

    @model(variant="query")
    class CombinedLeaf(Combined):
        pass

    assert StackedLeaf(identifier="q").model_dump() == CombinedLeaf(identifier="q").model_dump()
    with pytest.raises(ValueError, match="Duplicate tag 'request'"):
        build_adapter(Root, variants=[StackedLeaf, CombinedLeaf])
    with pytest.raises(TypeError, match="open family"):
        model(tag="method", variant="request")(DiscriminatedModel)
    with pytest.raises(ValueError, match="non-empty"):
        model(tag="method", variant="")
    with pytest.raises(TypeError, match="bind the inherited"):

        class Invalid[Method: str](Root):
            method: Method = tag_field()

        _ = Invalid


def test_empty_model_declaration_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one of tag or variant"):
        model()
    with pytest.raises(ValueError, match="at least one of tag or variant"):
        model(tag=None, variant=None)


def test_new_variant_preserves_parent_and_sibling_behavior() -> None:
    parent_schema = Request.model_json_schema()
    query = Query(identifier="q", text="hello")
    write = Write(identifier="w", value=1)

    @model(variant="later")
    class Later(Request):
        pass

    assert Request.model_json_schema() == parent_schema
    assert (
        Request.model_validate({"identifier": "r", "method": "open"}).model_dump()["method"]
        == "open"
    )
    assert Query(identifier="q", text="hello") == query
    assert Write(identifier="w", value=1) == write
    later = Later(identifier="l")
    assert later.model_dump()["method"] == "later"
    adapter = build_adapter(Message, variants=[Query, Write, Later])
    for value in (query, write, later):
        restored = adapter.validate_json(adapter.dump_json(value))
        assert type(restored) is type(value)
        assert restored == value


def test_runtime_field_names_support_multilevel_dispatch_and_copy_protection() -> None:
    @model(tag="request-type")
    class Root(IdentityDiscriminatedModel):
        identifier: str

    @model(tag="class", variant="request")
    class RequestWithKeyword(Root):
        pass

    @model(variant="query")
    class Leaf(RequestWithKeyword):
        text: str

    adapter = build_adapter(Root, variants=[Leaf])
    fields = bind_variants([Leaf])

    class Document(BaseModel):
        values: list[Annotated[Root, fields]]

    explicit = {"request-type": "request", "class": "query"}
    value = Leaf(identifier="q", text="hello", **explicit)
    assert value.model_dump(exclude_unset=True) == {
        "identifier": "q",
        "text": "hello",
        **explicit,
    }
    assert type(adapter.validate_json(adapter.dump_json(value))) is Leaf
    document = Document(values=[value.model_copy(update={"text": "copy"})])
    restored = Document.model_validate_json(document.model_dump_json(exclude_unset=True))
    assert restored == document and type(restored.values[0]) is Leaf
    for name in explicit:
        with pytest.raises(ValueError, match="bound discriminator"):
            value.model_copy(update={name: explicit[name]})
        with pytest.raises(ValidationError, match="frozen_field"):
            setattr(value, name, "wrong")


def test_decorated_explicit_tag_input_is_validated_at_runtime() -> None:
    tags: dict[str, Any] = {"kind": "request", "method": "query"}
    value = Query(identifier="q", text="hello", **tags)
    assert value.model_dump()["method"] == "query"
    wrong: dict[str, Any] = {**tags, "method": "wrong"}
    with pytest.raises(ValidationError, match="literal_error"):
        Query(identifier="q", text="hello", **wrong)
    for name in tags:
        with pytest.raises(ValueError, match="bound discriminator"):
            value.model_copy(update={name: tags[name]})
    copy = value.model_copy(update={"text": "copy"})
    assert_type(copy, Query)
    document = Envelope(primary=copy, requests=[copy], by_name={"q": copy})
    assert Envelope.model_validate_json(document.model_dump_json(exclude_unset=True)) == document
