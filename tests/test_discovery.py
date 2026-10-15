"""Verify batched entry point loading, source scopes, and native model bindings."""

from collections.abc import Iterator
from importlib import metadata
from pathlib import Path
from typing import Annotated, Any, Literal, cast

import pytest
from pydantic import BaseModel, TypeAdapter

from typebind import (
    DiscriminatedModel,
    bind_variants,
    build_adapter,
    discover_variants,
    select_variants,
    tag_field,
)


class Message[Kind: str](DiscriminatedModel):
    kind: Kind = tag_field()
    identifier: str


class Request[Method: str](Message[Literal["request"]]):
    method: Method = tag_field()
    sequence: int


class Query(Request[Literal["query"]]):
    expression: str


class Batch[Mode: str](Request[Literal["batch"]]):
    mode: Mode = tag_field()


class Stream(Batch[Literal["stream"]]):
    cursor: str


class LocalQuery(Request[Literal["local"]]):
    expression: str


class Template[Kind: str](DiscriminatedModel):
    kind: Kind = tag_field()


class BasicTemplate(Template[Literal["query"]]):
    content: str


@pytest.fixture
def publications(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[list[metadata.EntryPoint], list[metadata.EntryPoint], list[None]]:
    """Provide real metadata records while observing enumeration and loading."""
    entries: list[metadata.EntryPoint] = []
    loaded: list[metadata.EntryPoint] = []
    enumerated: list[None] = []
    exports: dict[str, object] = {
        "plugin:Query": Query,
        "plugin:Stream": Stream,
        "plugin:BasicTemplate": BasicTemplate,
        "plugin:Request": Request,
        "plugin:invalid": 42,
    }
    for name, text in (
        (
            "first",
            "[example.extensions]\nquery = plugin:Query\ntemplate = plugin:BasicTemplate\n"
            "[example.storage]\nstream = plugin:Stream\nquery = plugin:Query\n",
        ),
        ("second", "[example.extensions]\nquery = plugin:Stream\n"),
        ("unselected", "[other.group]\nbroken = missing:Broken\n"),
    ):
        directory = tmp_path / f"{name}-1.0.dist-info"
        directory.mkdir()
        (directory / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n")
        (directory / "entry_points.txt").write_text(text)
        entries.extend(metadata.Distribution.at(directory).entry_points)

    def enumerate_entries() -> metadata.EntryPoints:
        enumerated.append(None)
        return metadata.EntryPoints(entries)

    def load(entry: metadata.EntryPoint) -> object:
        loaded.append(entry)
        return exports[entry.value]

    monkeypatch.setattr(metadata, "entry_points", enumerate_entries)
    monkeypatch.setattr(metadata.EntryPoint, "load", load)
    return entries, loaded, enumerated


def test_batch_many_to_many_and_overlapping_families(
    publications: tuple[list[metadata.EntryPoint], list[metadata.EntryPoint], list[None]],
) -> None:
    entries, loaded, enumerated = publications
    found = discover_variants(
        groups=["example.extensions", "example.storage", "example.extensions"]
    )
    assert len(enumerated) == 1
    assert [id(entry) for entry in loaded] == [id(entry) for entry in entries[:-1]]
    assert len(found) == 5
    assert found[0].entry_point is entries[0]
    distribution = found[-1].entry_point.dist
    assert distribution is not None
    assert distribution.metadata["Name"] == "second"
    assert select_variants(found, family=Message[Any]) == (Query, Stream)
    assert select_variants(found, family=Request[Any]) == (Query, Stream)
    assert select_variants(found, family=Batch[Any]) == (Stream,)
    assert select_variants(found, family=Template[Any]) == (BasicTemplate,)
    assert select_variants(found, family=Message) == (Query, Stream)
    assert select_variants(found, family=Message[str]) == (Query, Stream)
    assert len(enumerated) == 1
    assert len(loaded) == 5


def test_scope_filtering_precedes_deduplication_and_extras_ignore_groups(
    publications: tuple[list[metadata.EntryPoint], list[metadata.EntryPoint], list[None]],
) -> None:
    found = discover_variants(groups={"example.extensions", "example.storage"})
    assert select_variants(found, family=Message[Any], groups={"example.storage"}) == (
        Stream,
        Query,
    )
    assert select_variants(found, family=Template[Any], groups={"example.storage"}) == ()
    assert select_variants(found, family=Message[Any], groups=set()) == ()
    assert select_variants(found, family=Message[Any], groups={"absent"}) == ()
    assert select_variants(
        iter(found),
        family=Request[Any],
        groups={"example.storage"},
        extra_variants=iter([LocalQuery, BasicTemplate, Query, LocalQuery]),
    ) == (Stream, Query, LocalQuery)
    assert select_variants(
        found, family=Message[Any], groups=set(), extra_variants=[LocalQuery, BasicTemplate]
    ) == (LocalQuery,)
    assert len(found) == 5


def test_metadata_predicate_runs_before_load_and_only_for_selected_groups(
    publications: tuple[list[metadata.EntryPoint], list[metadata.EntryPoint], list[None]],
) -> None:
    _, loaded, _ = publications
    inspected: list[str] = []

    def include(entry: metadata.EntryPoint) -> bool:
        inspected.append(entry.group)
        distribution = entry.dist
        assert distribution is not None
        return distribution.metadata["Name"] == "first" and entry.name == "query"

    found = discover_variants(groups={"example.extensions"}, include=include)
    assert [item.model for item in found] == [Query]
    assert len(loaded) == 1
    assert inspected == ["example.extensions"] * 3


def test_empty_and_missing_groups(
    publications: tuple[list[metadata.EntryPoint], list[metadata.EntryPoint], list[None]],
) -> None:
    _, loaded, enumerated = publications
    assert discover_variants(groups=[]) == ()
    assert enumerated == []
    assert discover_variants(groups={"missing"}) == ()
    assert len(enumerated) == 1
    assert loaded == []


def test_loading_errors_include_source_and_preserve_type(
    publications: tuple[list[metadata.EntryPoint], list[metadata.EntryPoint], list[None]],
) -> None:
    with pytest.raises(KeyError, match="missing:Broken") as error:
        discover_variants(groups={"other.group"})
    assert error.value.__cause__ is None
    for value in ("unselected", "other.group", "broken", "missing:Broken"):
        assert value in "\n".join(error.value.__notes__)


@pytest.mark.parametrize("exception_type", [ModuleNotFoundError, ImportError, ValueError])
def test_loading_preserves_exception_identity_cause_and_existing_notes(
    publications: tuple[list[metadata.EntryPoint], list[metadata.EntryPoint], list[None]],
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[Exception],
) -> None:
    cause = LookupError("original cause")
    original = exception_type("original message")
    original.__cause__ = cause
    original.add_note("existing note")

    def fail(entry: metadata.EntryPoint) -> object:
        assert entry.group == "example.extensions"
        raise original

    monkeypatch.setattr(metadata.EntryPoint, "load", fail)
    with pytest.raises(exception_type) as error:
        discover_variants(groups={"example.extensions"})
    assert error.value is original
    assert error.value.__cause__ is cause
    assert str(error.value) == "original message"
    assert error.value.__notes__[0] == "existing note"
    assert len(error.value.__notes__) == 2
    assert "distribution='first'" in error.value.__notes__[1]


def test_invalid_exports_are_not_silently_skipped(
    publications: tuple[list[metadata.EntryPoint], list[metadata.EntryPoint], list[None]],
) -> None:
    entries, _, _ = publications
    entries.append(metadata.EntryPoint(name="bad", value="plugin:invalid", group="bad.group"))
    with pytest.raises(TypeError, match="DiscriminatedModel class") as error:
        discover_variants(groups={"bad.group"})
    for value in ("<unknown>", "bad.group", "bad", "plugin:invalid", "42"):
        assert value in str(error.value)


def test_predicate_failures_propagate(
    publications: tuple[list[metadata.EntryPoint], list[metadata.EntryPoint], list[None]],
) -> None:
    _, loaded, _ = publications

    def include(entry: metadata.EntryPoint) -> bool:
        raise LookupError(entry.name)

    with pytest.raises(LookupError, match="query"):
        discover_variants(groups={"example.extensions"}, include=include)
    assert loaded == []


def test_open_export_does_not_find_unpublished_descendants(
    publications: tuple[list[metadata.EntryPoint], list[metadata.EntryPoint], list[None]],
) -> None:
    entries, _, _ = publications
    entries.append(metadata.EntryPoint(name="request", value="plugin:Request", group="open.group"))
    found = discover_variants(groups={"open.group"})
    variants = select_variants(found, family=Message[Any])
    assert variants == (Request,)
    with pytest.raises(TypeError, match="tagged leaf"):
        build_adapter(Message[Any], variants=variants)


@pytest.mark.parametrize(
    "family", [BaseModel, object, 42, Query(identifier="q", sequence=1, expression="x")]
)
def test_selection_rejects_non_model_class_family(family: object) -> None:
    with pytest.raises(TypeError, match="family must be a DiscriminatedModel class"):
        select_variants((), family=cast(Any, family))


def test_selection_preserves_named_class_and_closed_specialization_constraints(
    publications: tuple[list[metadata.EntryPoint], list[metadata.EntryPoint], list[None]],
) -> None:
    class DetailedQuery(Query):
        detail: str

    class SiblingQuery(Request[Literal["query"]]):
        other: str

    found = discover_variants(groups={"example.extensions", "example.storage"})
    extras = [DetailedQuery, SiblingQuery, LocalQuery]
    assert select_variants(found, family=DiscriminatedModel, extra_variants=extras) == (
        Query,
        BasicTemplate,
        Stream,
        DetailedQuery,
        SiblingQuery,
        LocalQuery,
    )
    assert select_variants(found, family=Query, extra_variants=extras) == (Query, DetailedQuery)
    assert select_variants(found, family=DetailedQuery, extra_variants=extras) == (DetailedQuery,)
    assert select_variants(found, family=Request[Literal["query"]], extra_variants=extras) == (
        Query,
        DetailedQuery,
        SiblingQuery,
    )
    assert select_variants(found, family=Request[Literal["batch"]], extra_variants=extras) == (
        Stream,
    )
    assert select_variants(found, family=Batch[Literal["stream"]], extra_variants=extras) == (
        Stream,
    )
    assert select_variants(found, family=Request[Literal["absent"]], extra_variants=extras) == ()
    assert select_variants(found, family=BasicTemplate) == (BasicTemplate,)
    # Selection does not loosen adapter construction's open-family contract.
    for closed in (DiscriminatedModel, Query, Request[Literal["query"]]):
        with pytest.raises(TypeError, match="open DiscriminatedModel family"):
            build_adapter(closed, variants=[Query])


@pytest.mark.parametrize("groups", ["example.extensions", ""])
def test_single_string_scope_is_rejected_before_any_enumeration_or_consumption(
    publications: tuple[list[metadata.EntryPoint], list[metadata.EntryPoint], list[None]],
    groups: str,
) -> None:
    entries, loaded, enumerated = publications
    # A character from the mistaken string is itself a valid, unwanted group.
    entries.append(metadata.EntryPoint(name="character", value="plugin:Stream", group="e"))
    with pytest.raises(TypeError, match="not a single string"):
        discover_variants(groups=groups)
    assert loaded == enumerated == []

    def extras() -> Iterator[type[DiscriminatedModel]]:
        pytest.fail("Invalid scope must not consume variants")
        yield Query

    with pytest.raises(TypeError, match="not a single string"):
        select_variants((), family=Message[Any], groups=groups, extra_variants=extras())


def test_scope_iterables_are_consumed_once(
    publications: tuple[list[metadata.EntryPoint], list[metadata.EntryPoint], list[None]],
) -> None:
    visits: list[str] = []

    def groups() -> Iterator[str]:
        for group in ("example.extensions", "example.storage", "example.storage"):
            visits.append(group)
            yield group

    found = discover_variants(groups=groups())
    assert len(found) == 5
    assert visits == ["example.extensions", "example.storage", "example.storage"]
    visits.clear()
    assert select_variants(found, family=Message[Any], groups=groups()) == (Query, Stream)
    assert visits == ["example.extensions", "example.storage", "example.storage"]
    assert select_variants(
        found, family=Message[Any], groups=iter(()), extra_variants=[LocalQuery]
    ) == (LocalQuery,)


def test_conflicts_are_checked_only_when_assembling_the_selected_scope(
    publications: tuple[list[metadata.EntryPoint], list[metadata.EntryPoint], list[None]],
) -> None:
    class AlternateQuery(Request[Literal["query"]]):
        alternate: str

    found = discover_variants(groups={"example.extensions"})
    variants = select_variants(found, family=Message[Any], extra_variants=[AlternateQuery])
    with pytest.raises(ValueError, match="Duplicate tag 'query'"):
        build_adapter(Message[Any], variants=variants)
    isolated = select_variants(
        found, family=Message[Any], groups=set(), extra_variants=[AlternateQuery]
    )
    build_adapter(Message[Any], variants=isolated)
    build_adapter(Template[Any], variants=select_variants(found, family=Template[Any]))


def test_discovered_and_local_variants_preserve_nested_roundtrips(
    publications: tuple[list[metadata.EntryPoint], list[metadata.EntryPoint], list[None]],
) -> None:
    found = discover_variants(groups={"example.extensions", "example.storage"})
    selected = select_variants(found, family=Message[Any], extra_variants=[LocalQuery])
    binding = bind_variants(selected)
    template_binding = bind_variants(select_variants(found, family=Template[Any]))

    class Envelope(BaseModel):
        primary: Annotated[Message[Any], binding]
        requests: list[Annotated[Request[Any], binding]]
        by_name: dict[str, Annotated[Message[Any], binding]]
        optional: Annotated[Request[Any], binding] | None = None
        template: Annotated[Template[Any], template_binding]

    query = Query(identifier="q", sequence=1, expression="select")
    stream = Stream(identifier="s", sequence=2, cursor="next")
    local = LocalQuery(identifier="l", sequence=3, expression="local")
    envelope = Envelope(
        primary=query,
        requests=[stream, local],
        by_name={"local": local},
        optional=local,
        template=BasicTemplate(content="hello"),
    )
    assert stream.model_dump(exclude_defaults=True)["mode"] == "stream"
    restored = Envelope.model_validate_json(envelope.model_dump_json(warnings="error"))
    assert restored == envelope
    assert type(restored.primary) is Query
    assert type(restored.requests[0]) is Stream
    assert type(restored.requests[1]) is LocalQuery
    assert type(restored.by_name["local"]) is LocalQuery
    assert type(restored.optional) is LocalQuery
    assert type(restored.template) is BasicTemplate
    adapter = TypeAdapter(Envelope)
    adapter.rebuild(force=True)
    assert adapter.validate_json(adapter.dump_json(envelope, warnings="error")) == envelope
    assert adapter.json_schema()["$defs"]
    root = build_adapter(Message[Any], variants=selected)
    assert type(root.validate_json(root.dump_json(local))) is LocalQuery
    narrow = select_variants(found, family=Request[Literal["batch"]], extra_variants=[LocalQuery])
    narrow_binding = bind_variants(narrow)

    class BatchEnvelope(BaseModel):
        messages: list[Annotated[Message[Any], narrow_binding]]

    batches = BatchEnvelope(messages=[stream])
    restored_batches = BatchEnvelope.model_validate_json(batches.model_dump_json())
    assert restored_batches == batches
    assert type(restored_batches.messages[0]) is Stream


def test_discovery_snapshots_are_independent(
    publications: tuple[list[metadata.EntryPoint], list[metadata.EntryPoint], list[None]],
) -> None:
    entries, _, enumerated = publications
    first = discover_variants(groups={"example.extensions"})
    entries.clear()
    assert discover_variants(groups={"example.extensions"}) == ()
    assert len(enumerated) == 2
    assert select_variants(first, family=Message[Any]) == (Query, Stream)
