"""Exercise declarations, dispatch, isolation, and serialization boundaries."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Annotated, Any, Literal, assert_type, cast, get_type_hints
from uuid import UUID

import pytest
from pydantic import (
    BaseModel,
    Field,
    ModelWrapValidatorHandler,
    PydanticUserError,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from typebind import DiscriminatedModel, bind_variants, build_adapter, tag_field

type QueryTag = Literal["query"]
type NestedQueryTag = QueryTag


class FirstTags:
    type Tag = Literal["first"]


class SecondTags:
    type Tag = Literal["second"]


class Message[Kind: str](DiscriminatedModel):
    kind: Kind = tag_field()
    identifier: str


class Request[Method: str](Message[Literal["request"]]):
    method: Method = tag_field()
    sequence: int

    @field_validator("sequence")
    @classmethod
    def nonnegative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("sequence must be nonnegative")
        return value


class Query(Request[Literal["query"]]):
    expression: str


class Write(Request[Literal["write"]]):
    value: int


class Event(Message[Literal["event"]]):
    description: str


class Batch[Mode: str](Request[Literal["batch"]]):
    mode: Mode = tag_field()
    size: int


class Stream(Batch[Literal["stream"]]):
    cursor: str


VARIANTS = [Query, Write, Event, Stream]
BINDING = bind_variants(VARIANTS)


class Document(BaseModel):
    primary: Annotated[Message[Any], BINDING]
    requests: list[Annotated[Request[Any], BINDING]]
    by_name: dict[str, Annotated[Message[Any], BINDING]]
    optional: Annotated[Message[Any], BINDING] | None = None


Ping = Message[Literal["ping"]]
ALIAS_BINDING = bind_variants([*VARIANTS, Ping])
type MessageValue = Annotated[Message[Any], ALIAS_BINDING]
type RequestValue = Annotated[Request[Any], ALIAS_BINDING]


class AliasedDocument(BaseModel):
    primary: MessageValue
    requests: list[RequestValue]
    by_name: dict[str, MessageValue]
    optional: RequestValue | None = None


def test_brackets_preserve_fields_and_types() -> None:
    query = Query(identifier="q", sequence=2, expression="hello")
    assert_type(query, Query)
    assert_type(query.identifier, str)
    assert_type(query.sequence, int)
    assert_type(query.expression, str)
    assert isinstance(query, Request)
    assert isinstance(query, Message)
    assert query.model_dump() == {
        "identifier": "q",
        "sequence": 2,
        "expression": "hello",
        "kind": "request",
        "method": "query",
    }
    with pytest.raises(ValidationError, match="literal_error"):
        Query.model_validate({**query.model_dump(), "kind": "event"})
    with pytest.raises(ValidationError, match="nonnegative"):
        Query(identifier="q", sequence=-1, expression="hello")


def test_generic_consumers_preserve_the_tag_parameter() -> None:
    def get_method[T: str](request: Request[T]) -> T:
        return request.method

    query = Query(identifier="q", sequence=1, expression="x")
    assert_type(get_method(query), Literal["query"])
    assert get_method(query) == "query"
    # Python 3.14 defers annotation evaluation; inspect it to exercise the native
    # Request[T] specialization as well as checking the function statically.
    annotation = get_type_hints(get_method)["request"]
    assert annotation.model_fields["method"].annotation is get_method.__type_params__[0]
    assert annotation.__pydantic_generic_metadata__["origin"] is Request


def test_closed_union_fields_preserve_concrete_subtypes() -> None:
    class ClosedDocument(BaseModel):
        primary: Query | Write
        requests: list[Query | Write | Stream]

    query = Query(identifier="q", sequence=1, expression="x")
    stream = Stream(identifier="s", sequence=2, size=4, cursor="next")
    document = ClosedDocument(primary=query, requests=[query, stream])
    adapter = TypeAdapter(ClosedDocument)
    restored = adapter.validate_json(adapter.dump_json(document, warnings="error"))
    assert restored == document
    assert type(restored.primary) is Query
    assert type(restored.requests[1]) is Stream


def test_three_levels_and_different_depths() -> None:
    adapter = build_adapter(Message[Any], variants=VARIANTS)
    assert_type(adapter, TypeAdapter[Message[Any]])
    stream = Stream(identifier="s", sequence=1, size=4, cursor="next")
    restored = adapter.validate_json(adapter.dump_json(stream, warnings="error"))
    assert_type(restored, Message[Any])
    assert type(restored) is Stream
    assert restored == stream
    schema = adapter.json_schema()
    assert schema["discriminator"]["propertyName"] == "kind"
    assert "method" in str(schema) and "mode" in str(schema)
    event = Event(identifier="e", description="event")
    assert type(adapter.validate_python(event.model_dump())) is Event


def test_nested_roundtrip_and_original_envelope_identity() -> None:
    query = Query(identifier="q", sequence=1, expression="find")
    stream = Stream(identifier="s", sequence=2, size=4, cursor="next")
    event = Event(identifier="e", description="event")
    adapter = TypeAdapter(Document)
    document = adapter.validate_python(
        {"primary": query, "requests": [stream, query], "by_name": {"e": event}}
    )
    assert_type(adapter, TypeAdapter[Document])
    wire = adapter.dump_json(document, warnings="error")
    restored = adapter.validate_json(wire)
    assert_type(restored, Document)
    assert type(restored) is Document
    assert type(restored.primary) is Query
    assert type(restored.requests[0]) is Stream
    assert type(restored.requests[1]) is Query
    assert type(restored.by_name["e"]) is Event
    assert restored == document
    assert adapter.dump_json(restored, warnings="error") == wire
    assert adapter.validate_python(adapter.dump_python(document)) == document
    adapter.json_schema()


def test_duplicate_tags_checked_at_build_time() -> None:
    class AnotherQuery(Request[Literal["query"]]):
        different: bool

    for variants in ([Query, AnotherQuery], [AnotherQuery, Query]):
        with pytest.raises(ValueError, match="Duplicate tag 'query'.*Request.method") as error:
            build_adapter(Message[Any], variants=variants)
        message = str(error.value)
        assert f"{Request.__module__}.{Request.__qualname__}.method" in message
        for variant in variants:
            assert f"{variant.__module__}.{variant.__qualname__}" in message
        assert "Use distinct Literal tags" in message
    build_adapter(Message[Any], variants=[Query, Query])


def test_duplicate_intermediate_branch_rejected() -> None:
    class OtherRequest[Method: str](Message[Literal["request"]]):
        action: Method = tag_field()

    class OtherQuery(OtherRequest[Literal["query"]]):
        pass

    with pytest.raises(ValueError, match="Duplicate tag 'request'.*Message.kind") as error:
        build_adapter(Message[Any], variants=[Query, OtherQuery])
    message = str(error.value)
    for model in (Message, Request, OtherRequest):
        assert f"{model.__module__}.{model.__qualname__}" in message


def test_same_tag_under_different_parents_is_valid() -> None:
    class Response[Method: str](Message[Literal["response"]]):
        method: Method = tag_field()

    class QueryResponse(Response[Literal["query"]]):
        answer: str

    adapter = build_adapter(Message[Any], variants=[Query, QueryResponse])
    instance = QueryResponse(identifier="r", answer="yes")
    assert type(adapter.validate_json(adapter.dump_json(instance))) is QueryResponse


def test_no_skipping_or_rebinding_ancestors() -> None:
    with pytest.raises(TypeError, match="Too many arguments"):
        Message.__class_getitem__((Literal["request"], Literal["query"]))
    with pytest.raises(TypeError, match="not a generic class"):
        Query.__class_getitem__(Literal["other"])
    with pytest.raises(TypeError, match="Inherit from Message") as error:

        class _Untagged(Message[Any]):
            pass

        assert issubclass(_Untagged, Message)
    assert "_Untagged.kind" in str(error.value)
    assert "Message[Literal['tag']]" in str(error.value)
    assert "Message[Any][" not in str(error.value)

    with pytest.raises(TypeError, match="cannot be overridden") as error:
        from pydantic import create_model

        create_model("ChangedTag", __base__=Query, kind=(Literal["other"], "other"))
    assert "ChangedTag.kind:" in str(error.value)
    assert "fixes it to 'request', got 'other'" in str(error.value)


def test_unknown_missing_tags_and_incomplete_variants() -> None:
    adapter = build_adapter(Message[Any], variants=[Query])
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        adapter.validate_python({"kind": "unknown"})
    with pytest.raises(ValidationError, match="union_tag_not_found"):
        adapter.validate_python({"identifier": "x"})
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        adapter.validate_python({"kind": "request", "method": "unknown"})
    with pytest.raises(ValueError, match="No variants"):
        build_adapter(Message[Any], variants=[])
    with pytest.raises(TypeError, match="leaf"):
        build_adapter(Message[Any], variants=[Request])
    specialized = Request[Literal["anonymous"]]
    anonymous_adapter = build_adapter(Message[Any], variants=[specialized])
    instance = specialized(identifier="a", sequence=1)
    assert (
        type(anonymous_adapter.validate_json(anonymous_adapter.dump_json(instance))) is specialized
    )


def test_variant_bindings_are_explicit_independent_snapshots() -> None:
    before = Message.model_json_schema()
    variants: list[type[DiscriminatedModel]] = [Query]
    query_binding = bind_variants(variants)
    variants.append(Write)

    class QueryDocument(BaseModel):
        primary: Annotated[Message[Any], query_binding]

    class ExpandedDocument(BaseModel):
        primary: Annotated[Message[Any], bind_variants(variants)]

    write = Write(identifier="w", sequence=1, value=42)
    data = {"primary": write.model_dump()}
    assert type(ExpandedDocument.model_validate(data).primary) is Write
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        QueryDocument.model_validate(data)
    assert Message.model_json_schema() == before
    # Rebuild uses the original bound annotation, not patched adapter internals.
    adapter = TypeAdapter(ExpandedDocument)
    adapter.rebuild(force=True)
    assert type(adapter.validate_python(data).primary) is Write


def test_recursive_family_fields() -> None:
    class Node[Kind: str](DiscriminatedModel):
        kind: Kind = tag_field()
        label: str

    class Leaf(Node[Literal["leaf"]]):
        value: int

    class Group(Node[Literal["group"]]):
        children: list[Annotated["Leaf | Group", Field(discriminator="kind")]]

    adapter = build_adapter(Node[Any], variants=[Leaf, Group])
    node = adapter.validate_python(
        {"kind": "group", "label": "g", "children": [Leaf(label="l", value=3)]}
    )
    restored = adapter.validate_json(adapter.dump_json(node, warnings="error"))
    assert type(restored) is Group
    assert restored == node
    adapter.json_schema()


def test_model_validators_are_not_duplicated() -> None:
    from typing import Self

    class Checked[Kind: str](DiscriminatedModel):
        kind: Kind = tag_field()
        value: int

        @model_validator(mode="after")
        def increment(self) -> Self:
            self.value += 1
            return self

    class Concrete(Checked[Literal["concrete"]]):
        pass

    adapter = build_adapter(Checked[Any], variants=[Concrete])
    result = adapter.validate_python({"kind": "concrete", "value": 0})
    assert result.value == 1


def test_independent_families_and_missing_wrapped_family() -> None:
    from typing import Self

    class Separate[Kind: str](DiscriminatedModel):
        kind: Kind = tag_field()
        value: int

        @model_validator(mode="after")
        def unchanged(self) -> Self:
            return self

    class SeparateQuery(Separate[Literal["request"]]):
        pass

    class Pair(BaseModel):
        first: Annotated[Message[Any], bind_variants([Query])]
        second: Annotated[Separate[Any], bind_variants([SeparateQuery])]

    adapter = TypeAdapter(Pair)
    pair = adapter.validate_python(
        {
            "first": Query(identifier="q", sequence=0, expression="x"),
            "second": SeparateQuery(value=1),
        }
    )
    assert adapter.validate_json(adapter.dump_json(pair)) == pair
    with pytest.raises(ValueError, match="No variants.*Separate"):

        class MissingBinding(BaseModel):
            second: Annotated[Separate[Any], bind_variants([Query])]

        assert issubclass(MissingBinding, BaseModel)


def test_tags_have_literal_types_and_cannot_be_mutated() -> None:
    query = Query(identifier="q", sequence=1, expression="x")
    assert_type(query.kind, Literal["request"])
    assert_type(query.method, Literal["query"])
    with pytest.raises(ValidationError, match="frozen_field"):
        query.kind = "request"
    query.expression = "updated"
    assert query.expression == "updated"


def test_native_specialization_cache_and_family_fields_remain_unbound() -> None:
    assert Message[Literal["request"]] is Message[Literal["request"]]
    assert Request[Literal["query"]] is Request[Literal["query"]]
    assert Message.model_fields["kind"].annotation is Message.__type_params__[0]
    assert Request.model_fields["method"].annotation is Request.__type_params__[0]
    assert Message.model_fields["kind"].default_factory is not None
    assert not Message.model_fields["kind"].is_required()
    assert Query.model_fields["kind"].annotation == Literal["request"]
    assert Query.model_validate({"identifier": "q", "sequence": 1, "expression": "x"}).kind == (
        "request"
    )
    with pytest.raises(ValidationError, match="Field required"):
        Message[Any](identifier="unbound")


@pytest.mark.parametrize("annotation", [Literal[""], Literal["a", "b"], Literal[1], int])
def test_invalid_native_tag_specializations(annotation: object) -> None:
    # Dynamic input is deliberate: valid source code rejects some of these statically too.
    with pytest.raises(TypeError, match="single non-empty string Literal") as error:
        Message.__class_getitem__(cast(Any, annotation))
    assert f"{Message.__module__}.Message[" in str(error.value)
    assert ".kind:" in str(error.value)
    assert f"got {annotation!r}" in str(error.value)
    assert "Family[Literal['tag']]" in str(error.value)


def test_unbound_containers_are_not_transparently_rewritten() -> None:
    class DynamicDocument(BaseModel):
        primary: Message[Any]
        requests: list[Request[Any]]

    with pytest.raises(TypeError, match="open DiscriminatedModel family"):
        # Envelopes use TypeAdapter after explicitly binding their family fields.
        build_adapter(cast(Any, DynamicDocument), variants=[Query])


def test_discriminator_fields_require_validated_frozen_defaults() -> None:
    with pytest.raises(TypeError, match="declare its discriminator with tag_field"):

        class RequiredTag[Kind: str](DiscriminatedModel):
            kind: Kind

        assert issubclass(RequiredTag, DiscriminatedModel)

    with pytest.raises(TypeError, match="declare its discriminator with tag_field"):

        class MutableTag[Kind: str](DiscriminatedModel):
            kind: Kind = Field()

        assert issubclass(MutableTag, DiscriminatedModel)

    with pytest.raises(TypeError, match="str-bound type parameter") as error:

        class Unbounded[Tag](DiscriminatedModel):
            kind: Tag = tag_field()

        assert issubclass(Unbounded, DiscriminatedModel)
    assert "Unbounded.kind:" in str(error.value)
    assert "class Unbounded[Tag: str](...): kind: Tag = tag_field()" in str(error.value)


def test_named_literal_type_aliases_use_native_resolution() -> None:
    class AliasQuery(Request[QueryTag]):
        expression: str

    query = AliasQuery(identifier="q", sequence=1, expression="x")
    assert_type(query.method, Literal["query"])
    adapter = build_adapter(Message[Any], variants=[AliasQuery])
    assert type(adapter.validate_json(adapter.dump_json(query))) is AliasQuery
    with pytest.raises(ValueError, match="Duplicate tag 'query'"):
        build_adapter(Message[Any], variants=[Query, AliasQuery])


def test_automatic_defaults_reach_inherited_field_validators_as_strings() -> None:
    class Normalized[Kind: str](DiscriminatedModel):
        kind: Kind = tag_field()

        @field_validator("kind", mode="before")
        @classmethod
        def strip(cls, value: str) -> str:
            return value.strip()

    class Item(Normalized[Literal["item"]]):
        value: int

    original: dict[str, object] = {"value": 1}
    item = Item.model_validate(original)
    assert item.kind == "item"
    assert original == {"value": 1}
    assert Item(value=1) == item
    assert Item.model_validate_json('{"value":1}') == item
    assert item.model_fields_set == {"value"}
    assert item.model_dump(exclude_unset=True) == {"value": 1}
    assert item.model_dump(exclude_defaults=True) == {"value": 1}
    adapter = TypeAdapter(Item)
    wire = adapter.dump_json(item, exclude_defaults=True)
    assert adapter.validate_json(wire) == item
    with pytest.raises(PydanticUserError, match="mode='before'"):
        build_adapter(Normalized[Any], variants=[Item])
    with pytest.raises(ValidationError, match="literal_error"):
        Item.model_validate({"kind": "wrong", "value": 1})


@pytest.mark.parametrize("tags", [{}, {"kind": "request"}, {"method": "query"}])
def test_attribute_inputs_use_native_tag_defaults(tags: dict[str, str]) -> None:
    attributes = SimpleNamespace(identifier="q", sequence=1, expression="x", **tags)
    assert Query.model_validate(attributes, from_attributes=True) == Query(
        identifier="q", sequence=1, expression="x"
    )
    with pytest.raises(ValidationError, match="Field required"):
        Message[Any].model_validate(SimpleNamespace(identifier="q"), from_attributes=True)


def test_attribute_inputs_with_explicit_tags_round_trip() -> None:
    query = Query(identifier="q", sequence=1, expression="x")
    attributes = SimpleNamespace(**query.model_dump())
    restored = Query.model_validate(attributes, from_attributes=True)
    assert restored == query
    adapter = build_adapter(Message[Any], variants=VARIANTS)
    assert adapter.validate_python(attributes, from_attributes=True) == query
    assert adapter.validate_json(adapter.dump_json(restored, warnings="error")) == query


def test_bound_containers_use_native_model_methods_and_adapter_rebuild() -> None:
    query = Query(identifier="q", sequence=1, expression="x")
    document = Document(primary=query, requests=[query], by_name={"q": query})
    restored = Document.model_validate_json(document.model_dump_json(warnings="error"))
    assert restored == document
    assert type(restored.primary) is Query
    assert type(restored.requests[0]) is Query
    assert type(restored.by_name["q"]) is Query
    adapter = build_adapter(Message[Any], variants=VARIANTS)
    adapter.rebuild(force=True)
    assert type(adapter.validate_json(adapter.dump_json(query))) is Query


def test_conditional_tag_exclusion_is_rejected_at_declaration() -> None:
    def omit_tag(value: object) -> bool:
        return value == "item"

    with pytest.raises(TypeError, match="aliases, or exclusion") as error:

        class HiddenTag[Kind: str](DiscriminatedModel):
            kind: Annotated[Kind, Field(exclude_if=omit_tag)] = tag_field()

        assert issubclass(HiddenTag, DiscriminatedModel)
    assert "HiddenTag.kind:" in str(error.value)
    assert "Unsupported settings: exclude_if" in str(error.value)


def test_tag_field_is_the_single_source_of_the_discriminator() -> None:
    class Packet[Tag: str](DiscriminatedModel, extra="forbid"):
        operation: Annotated[Tag, Field(description="Wire operation")] = tag_field()
        payload: str

    class Ping(Packet[Literal["ping"]]):
        pass

    assert Packet.model_fields["operation"].description == "Wire operation"
    assert Packet.model_config.get("extra") == "forbid"
    ping = Ping(payload="hello")
    assert_type(ping.operation, Literal["ping"])
    assert ping.operation == "ping"
    assert Ping.model_fields["operation"].frozen is True
    adapter = build_adapter(Packet[Any], variants=[Ping])
    assert adapter.json_schema()["discriminator"]["propertyName"] == "operation"
    assert adapter.validate_json(adapter.dump_json(ping)) == ping
    with pytest.raises(ValidationError, match="extra_forbidden"):
        Ping.model_validate({"payload": "hello", "extra": 1})


def test_multiple_new_tag_fields_are_rejected() -> None:
    with pytest.raises(TypeError, match="exactly one new tag_field") as error:

        class Ambiguous[Tag: str](DiscriminatedModel):
            kind: Tag = tag_field()
            method: Tag = tag_field()

        assert issubclass(Ambiguous, DiscriminatedModel)
    assert "Ambiguous: found new tag fields ['kind', 'method']" in str(error.value)
    assert "child family" in str(error.value)


def test_inherited_tag_defaults_and_freezing_cannot_be_removed() -> None:
    from pydantic import create_model

    with pytest.raises(TypeError, match="must use tag_field"):
        create_model("UnfrozenQuery", __base__=Query, method=(Literal["query"], "query"))


def test_tag_field_requires_a_generic_discriminator() -> None:
    with pytest.raises(TypeError, match="str-bound type parameter"):

        class ConcreteTag(DiscriminatedModel):
            kind: Literal["concrete"] = tag_field()

        assert issubclass(ConcreteTag, DiscriminatedModel)


@pytest.mark.parametrize("family", [Message[Any], Request[Any], Batch[Any]])
def test_inherited_leaf_defaults_and_dispatch_match_native_field_schema(
    family: type[DiscriminatedModel],
) -> None:
    class DetailedStream(Stream):
        description: str

    item = DetailedStream(identifier="s", sequence=1, size=2, cursor="next", description="detail")
    data = item.model_dump()
    properties = DetailedStream.model_json_schema()["properties"]
    for name in ("kind", "method", "mode"):
        assert data[name] == properties[name]["const"] == properties[name]["default"]
    adapter = build_adapter(family, variants=[DetailedStream])
    restored = adapter.validate_python(data)
    assert type(restored) is DetailedStream
    assert restored == item
    assert adapter.validate_json(adapter.dump_json(item, warnings="error")) == item
    with pytest.raises(ValueError, match="Duplicate tag 'stream'.*Batch.mode"):
        build_adapter(family, variants=[Stream, DetailedStream])


def test_specialized_model_alias_is_constructible_and_typed() -> None:
    assert Ping is Message[Literal["ping"]]
    ping = Ping(identifier="p")
    assert_type(ping.kind, Literal["ping"])
    assert ping.kind == "ping"
    assert Ping.model_validate_json(ping.model_dump_json()) == ping


def test_native_container_type_aliases_preserve_nested_roundtrips() -> None:
    ping = Ping(identifier="p")
    query = Query(identifier="q", sequence=1, expression="x")
    stream = Stream(identifier="s", sequence=2, size=3, cursor="next")
    document = AliasedDocument(
        primary=ping,
        requests=[query, stream],
        by_name={"ping": ping, "query": query},
        optional=query,
    )
    assert_type(document.primary.identifier, str)
    assert_type(document.requests[0].sequence, int)
    restored = AliasedDocument.model_validate_json(document.model_dump_json(warnings="error"))
    assert restored == document
    assert type(restored.primary) is Ping
    assert type(restored.requests[0]) is Query
    assert type(restored.requests[1]) is Stream
    assert type(restored.by_name["ping"]) is Ping
    assert type(restored.optional) is Query
    adapter = TypeAdapter(AliasedDocument)
    adapter.rebuild(force=True)
    assert adapter.validate_python(adapter.dump_python(document)) == document
    assert adapter.validate_json(adapter.dump_json(document, warnings="error")) == document
    assert {"MessageValue", "RequestValue"} <= adapter.json_schema()["$defs"].keys()


def test_duplicate_tag_diagnostics_distinguish_extension_modules() -> None:
    from pydantic import create_model

    first = create_model("Query", __base__=Query, __module__="extension_one")
    second = create_model("Query", __base__=Query, __module__="extension_two")
    with pytest.raises(ValueError, match="Duplicate tag 'query'") as error:
        bind_variants([first, second])
    assert "extension_one.Query" in str(error.value)
    assert "extension_two.Query" in str(error.value)
    assert f"{Request.__module__}.{Request.__qualname__}.method" in str(error.value)


def test_removed_inherited_tag_reports_its_declaration() -> None:
    from typing import ClassVar

    from pydantic import create_model

    # Construct an invalid override dynamically; ClassVar is not a field type.
    removed_field = cast(Any, ClassVar)[str]
    with pytest.raises(TypeError, match="cannot be removed") as error:
        create_model("RemovedTag", __base__=Query, kind=(removed_field, "request"))
    assert "RemovedTag.kind:" in str(error.value)
    assert f"{Query.__module__}.{Query.__qualname__}" in str(error.value)
    assert "Keep the inherited model field" in str(error.value)


def test_construction_does_not_scan_or_resolve_tag_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    class AliasQuery(Request[NestedQueryTag]):
        expression: str

    variants = [Query, Stream, AliasQuery]
    samples = [
        Query(identifier="q", sequence=1, expression="x"),
        Stream(identifier="s", sequence=1, size=2, cursor="next"),
        AliasQuery(identifier="a", sequence=1, expression="alias"),
    ]
    adapters = [build_adapter(Message[Any], variants=[variant]) for variant in variants]

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Repeated construction must not compile tag schemas")

    with pytest.raises(ValueError, match="Duplicate tag 'query'"):
        bind_variants([Query, AliasQuery])
    monkeypatch.setattr("typebind._models.TypeAdapter", forbidden)
    monkeypatch.setattr("typebind._models._tag_fields", forbidden)
    monkeypatch.setattr("typebind._models._literal_tag", forbidden)
    for _ in range(5):
        for sample, adapter in zip(samples, adapters, strict=True):
            data = sample.model_dump(exclude={"kind", "method", "mode"})
            assert type(sample)(**data) == sample
            assert adapter.validate_json(adapter.dump_json(sample, warnings="error")) == sample


def test_native_defaults_preserve_alias_identity() -> None:
    # Equal alias names in independent scopes must not conflate their values.
    class FirstItem(Message[FirstTags.Tag]):
        pass

    class SecondItem(Message[SecondTags.Tag]):
        pass

    first_model, second_model = FirstItem, SecondItem
    adapter = build_adapter(Message[Any], variants=[first_model, second_model, Query])
    expected = [first_model(identifier="a"), second_model(identifier="b")]
    for model, item, tag in zip(
        (first_model, second_model), expected, ("first", "second"), strict=True
    ):
        restored = adapter.validate_json(adapter.dump_json(item, warnings="error"))
        assert type(restored) is model
        assert restored == model(identifier=item.model_dump()["identifier"])
        assert restored.model_dump()["kind"] == tag
    query = Query(identifier="q", sequence=1, expression="x")
    assert adapter.validate_json(adapter.dump_json(query)) == query
    with pytest.raises(ValidationError, match="literal_error"):
        Query.model_validate({**query.model_dump(), "method": "wrong"})


def test_json_contract_restores_nested_payloads_and_rejects_unknown_tags() -> None:
    class Record(Message[Literal["record"]]):
        created_at: datetime
        record_id: UUID

    binding = bind_variants([Record, Ping])

    class Envelope(BaseModel):
        records: list[Annotated[Message[Any], binding]]

    record = Record(
        identifier="r",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        record_id=UUID("12345678-1234-5678-1234-567812345678"),
    )
    envelope = Envelope(records=[record, Ping(identifier="p")])
    wire = envelope.model_dump_json(warnings="error")
    restored = Envelope.model_validate_json(wire)
    assert restored == envelope
    assert type(restored.records[0]) is Record
    assert type(restored.records[1]) is Ping
    assert restored.records[0].created_at == record.created_at
    assert restored.records[0].record_id == record.record_id
    data = envelope.model_dump(mode="json", warnings="error")
    assert isinstance(data["records"][0]["created_at"], str)
    assert isinstance(data["records"][0]["record_id"], str)
    assert Envelope.model_validate(data) == envelope
    adapter = build_adapter(Message[Any], variants=[Record, Ping])
    assert type(adapter.validate_json(adapter.dump_json(record))) is Record
    limited = build_adapter(Message[Any], variants=[Ping])
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        limited.validate_json(adapter.dump_json(record))


@pytest.mark.parametrize("deferred", [False, True])
def test_native_defaults_survive_recursive_and_deferred_rebuild(deferred: bool) -> None:
    class Tree(Message[Literal["tree"]], defer_build=deferred):
        children: list["Tree"] = Field(default_factory=lambda: list[Tree]())

    assert Tree.model_rebuild(force=True)
    tree = Tree.model_validate({"identifier": "root", "children": [{"identifier": "child"}]})
    assert tree.kind == tree.children[0].kind == "tree"
    assert Tree.model_validate_json(tree.model_dump_json()) == tree
    adapter = build_adapter(Message[Any], variants=[Tree])
    assert adapter.validate_json(adapter.dump_json(tree)) == tree
    assert Tree.model_rebuild(force=True)
    assert Tree(identifier="new").kind == "tree"
    assert adapter.rebuild(force=True)
    assert adapter.validate_json(adapter.dump_json(tree)) == tree


def test_defaults_preserve_validator_order_context_and_single_execution() -> None:
    events: list[str] = []

    class Checked[Kind: str](DiscriminatedModel):
        kind: Kind = tag_field()

        @model_validator(mode="before")
        @classmethod
        def before(cls, value: object) -> object:
            events.append("before")
            return value

        @field_validator("kind", mode="before")
        @classmethod
        def field_before(cls, value: str, info: ValidationInfo) -> str:
            assert value == "checked"
            assert info.context == {"probe": True}
            events.append("field")
            return value

        @model_validator(mode="after")
        def after(self) -> "Checked[Kind]":
            events.append("after")
            return self

        @model_validator(mode="wrap")
        @classmethod
        def wrap(
            cls, value: object, handler: ModelWrapValidatorHandler["Checked[Kind]"]
        ) -> "Checked[Kind]":
            events.append("enter")
            result = handler(value)
            events.append("exit")
            return result

    class Item(Checked[Literal["checked"]]):
        pass

    for data in ({}, {"kind": "checked"}):
        events.clear()
        item = Item.model_validate(data, context={"probe": True})
        assert item.kind == "checked"
        assert events == ["enter", "before", "field", "after", "exit"]


def test_normal_dumps_keep_tags_and_lossy_dumps_do_not_dispatch() -> None:
    query = Query(identifier="q", sequence=1, expression="x")
    adapter = build_adapter(Message[Any], variants=VARIANTS)
    document = Document(primary=query, requests=[query], by_name={"q": query})
    data = document.model_dump()
    for nested in (data["primary"], data["requests"][0], data["by_name"]["q"]):
        assert nested["kind"] == "request"
        assert nested["method"] == "query"
    assert Document.model_validate_json(document.model_dump_json()) == document
    assert adapter.dump_python(query)["method"] == "query"
    assert adapter.validate_json(adapter.dump_json(query)) == query
    assert query.model_fields_set == {"identifier", "sequence", "expression"}
    for wire in (
        adapter.dump_json(query, exclude_defaults=True),
        adapter.dump_json(query, exclude_unset=True),
    ):
        with pytest.raises(ValidationError, match="union_tag_not_found"):
            adapter.validate_json(wire)
        # A concrete model already identifies its type and can restore its defaults.
        assert Query.model_validate_json(wire) == query
    explicit = Query(kind="request", method="query", identifier="q", sequence=1, expression="x")
    assert {"kind", "method"} <= explicit.model_fields_set
    assert explicit.model_dump(exclude_unset=True)["kind"] == "request"
    assert "kind" not in explicit.model_dump(exclude_defaults=True)


def test_unchecked_construction_requires_explicit_tags_without_leaking_markers() -> None:
    with pytest.raises(ValueError, match="supply tags to model_construct"):
        Query.model_construct(identifier="q", sequence=1, expression="x")
    query = Query.model_construct(
        kind="request", method="query", identifier="q", sequence=1, expression="x"
    )
    assert query == Query(identifier="q", sequence=1, expression="x")
