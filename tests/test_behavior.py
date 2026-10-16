"""Behavior assembly validates declarations once and binds ordinary objects."""

from abc import abstractmethod
from importlib import metadata
from typing import Annotated, Any, Literal, Protocol, cast

import pytest
from pydantic import BaseModel, TypeAdapter

from typebind import (
    DiscriminatedModel,
    Implementation,
    assemble,
    bind_variants,
    discover,
    select_implementations,
    select_variants,
    tag_field,
)


class Message[Kind: str](DiscriminatedModel):
    kind: Kind = tag_field()
    identifier: str


class Request[Method: str](Message[Literal["request"]]):
    method: Method = tag_field()


class Query(Request[Literal["query"]]):
    expression: str


class Ping(Message[Literal["ping"]]):
    pass


class Other(DiscriminatedModel):
    value: int


class Describable(Protocol):
    @property
    @abstractmethod
    def model(self) -> Message[Any]: ...

    @abstractmethod
    def describe(self) -> str: ...


class Executable(Protocol):
    @property
    @abstractmethod
    def model(self) -> Request[Any]: ...

    @abstractmethod
    def execute(self) -> str: ...


class QueryObject(Implementation[Query], Describable, Executable):
    def describe(self) -> str:
        return self.model.expression

    def execute(self) -> str:
        return self.model.identifier


class PingObject(Implementation[Ping], Describable):
    def describe(self) -> str:
        return self.model.identifier


class Alternative(QueryObject):
    def describe(self) -> str:
        return "alternative"


class Envelope(BaseModel):
    messages: list[Annotated[Message[Any], bind_variants((Query, Ping))]]


def test_roundtrip_and_interface_identity() -> None:
    original = Envelope(messages=[Query(identifier="q", expression="hello"), Ping(identifier="p")])
    adapter = TypeAdapter(Envelope)
    restored = adapter.validate_json(adapter.dump_json(original))
    runtime = assemble(
        variants=(model for model in (Query, Ping)),
        implementations=iter((QueryObject, PingObject, QueryObject)),
        required_interfaces=(interface for interface in (Describable, Executable)),
    )
    query = restored.messages[0]
    describable = runtime.adapt(query, Describable)
    executable = runtime.adapt(query, Executable)
    assert type(query) is Query
    assert describable.model is query and executable.model is query
    assert describable is not executable
    assert describable.describe() == "hello"
    assert executable.execute() == "q"
    assert runtime.adapt(restored.messages[1], Describable).describe() == "p"
    assert Describable not in Query.__mro__
    with pytest.raises(LookupError, match="No implementation in this scope"):
        runtime.adapt(restored.messages[1], Executable)


def test_conflicts_coverage_and_scope() -> None:
    with pytest.raises(
        ValueError, match=r"Duplicate implementation.*Query.*Describable.*Alternative"
    ):
        assemble(variants=[Query], implementations=[QueryObject, Alternative])
    with pytest.raises(ValueError, match=r"Missing implementation.*Ping.*Describable"):
        assemble(
            variants=[Query, Ping], implementations=[QueryObject], required_interfaces=[Describable]
        )
    # Conflicts for unselected concrete targets do not poison a different scope.
    runtime = assemble(variants=[Ping], implementations=[QueryObject, Alternative, PingObject])
    assert runtime.adapt(Ping(identifier="p"), Describable).describe() == "p"
    first = assemble(variants=[Query], implementations=[QueryObject])
    second = assemble(variants=[Query], implementations=[Alternative])
    query = Query(identifier="q", expression="first")
    assert first.adapt(query, Describable).describe() == "first"
    assert second.adapt(query, Describable).describe() == "alternative"


def test_exact_scope_and_snapshot() -> None:
    class Child(Query):
        pass

    variants = [Query]
    implementations = [QueryObject]
    runtime = assemble(variants=variants, implementations=implementations)
    variants.clear()
    implementations.clear()
    assert runtime.adapt(Query(identifier="q", expression="ok"), Describable).describe() == "ok"
    with pytest.raises(LookupError):
        runtime.adapt(Child(identifier="q", expression="x"), Describable)
    with pytest.raises(ValueError, match="Missing implementation"):
        assemble(variants=[Child], implementations=[QueryObject], required_interfaces=[Describable])


def test_generic_forwarding_and_inherited_implementation() -> None:
    class Shared[T: DiscriminatedModel](Implementation[T]):
        pass

    class Middle[T: DiscriminatedModel](Shared[T]):
        pass

    class Forwarded(Middle[Query], Describable):
        def describe(self) -> str:
            return self.model.expression

    class Inherited(Forwarded):
        pass

    runtime = assemble(variants=[Query], implementations=[Inherited])
    assert (
        runtime.adapt(Query(identifier="q", expression="forwarded"), Describable).describe()
        == "forwarded"
    )


def test_no_reflection_discovery_or_validation_in_hot_path(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = assemble(
        variants=[Query], implementations=[QueryObject], required_interfaces=[Describable]
    )
    query = Query(identifier="q", expression="hello")

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("Cold-path work during adaptation")

    for name in (
        "get_type_hints",
        "get_original_bases",
        "get_protocol_members",
        "is_protocol",
        "signature",
        "family_base",
        "concrete_model",
    ):
        monkeypatch.setattr(f"typebind._behavior.{name}", forbidden)
    monkeypatch.setattr(metadata, "entry_points", forbidden)
    monkeypatch.setattr(Query, "model_validate", forbidden)
    for _ in range(10):
        view = runtime.adapt(query, Describable)
        assert view.model is query
        assert view.describe() == "hello"


def test_constructor_is_deferred_and_exceptions_preserved() -> None:
    failure = RuntimeError("constructor failure")

    class Fails(QueryObject):
        def __init__(self, model: Query) -> None:
            raise failure

    runtime = assemble(variants=[Query], implementations=[Fails])
    with pytest.raises(RuntimeError) as captured:
        runtime.adapt(Query(identifier="q", expression="x"), Describable)
    assert captured.value is failure

    class NeedsDependency(QueryObject):
        def __init__(self, model: Query, dependency: str) -> None:
            super().__init__(model)

    with pytest.raises(TypeError, match="dependency") as captured:
        assemble(variants=[Query], implementations=[NeedsDependency])
    assert "NeedsDependency" in captured.value.__notes__[-1]


def test_non_implementation_class_is_rejected() -> None:
    # Entry point exports may come from packages without static type checking.
    with pytest.raises(TypeError, match="Expected an Implementation class"):
        assemble(variants=[], implementations=[cast(Any, str)])


@pytest.mark.parametrize("bare", [Implementation, type("Bare", (Implementation,), {})])
def test_unspecialized_implementation_is_rejected(bare: type[object]) -> None:
    with pytest.raises(TypeError, match=r"Implementation\[") as error:
        assemble(variants=[], implementations=[cast(Any, bare)])
    assert f"{bare.__module__}.{bare.__qualname__}" in str(error.value)


def test_abstract_and_invalid_declarations() -> None:
    class Missing(Implementation[Query], Describable):
        pass

    with pytest.raises(TypeError, match="abstract"):
        assemble(variants=[Query], implementations=[Missing])
    with pytest.raises(TypeError, match="concrete"):
        assemble(variants=[Request[Any]], implementations=[])

    class Open(Implementation[Request[Any]]):
        pass

    with pytest.raises(TypeError, match="concrete"):
        assemble(variants=[Query], implementations=[Open])
    with pytest.raises(TypeError, match="Protocol"):
        assemble(variants=[], implementations=[], required_interfaces=[str])

    class NoModel(Protocol):
        def describe(self) -> str: ...

    with pytest.raises(TypeError, match="read-only model"):
        assemble(variants=[], implementations=[], required_interfaces=[NoModel])

    class BadReturn(Protocol):
        @property
        def model(self) -> int: ...

    with pytest.raises(TypeError, match="DiscriminatedModel") as error:
        assemble(variants=[], implementations=[], required_interfaces=[BadReturn])
    assert repr(int) in str(error.value)
    assert "BadReturn.model" in error.value.__notes__[-1]


def test_wrong_family_from_untyped_plugin() -> None:
    # Dynamic creation models an untyped external plugin; downstream typing has
    # a separate negative test for this declaration.
    def describe(self: object) -> str:
        return "wrong"

    wrong = cast(
        type[Implementation[Any]],
        type(
            "Wrong",
            (Implementation, Describable),
            {
                "__orig_bases__": (Implementation[Other], Describable),
                "describe": describe,
            },
        ),
    )
    with pytest.raises(TypeError, match="outside interface") as captured:
        assemble(variants=[Other], implementations=[wrong])
    assert "Wrong" in captured.value.__notes__[-1]


def test_shared_discovery_scope_and_extras(monkeypatch: pytest.MonkeyPatch) -> None:
    exports: dict[str, object] = {"model": Query, "impl": QueryObject, "alternate": Alternative}
    entries = metadata.EntryPoints(
        [
            metadata.EntryPoint(name="model", value="model", group="core"),
            metadata.EntryPoint(name="impl", value="impl", group="core"),
            metadata.EntryPoint(name="alias", value="impl", group="partner"),
            metadata.EntryPoint(name="alternate", value="alternate", group="alternate"),
            metadata.EntryPoint(name="excluded", value="broken", group="other"),
        ]
    )
    scans: list[None] = []
    loads: list[str] = []

    def scan() -> metadata.EntryPoints:
        scans.append(None)
        return entries

    def load(entry: metadata.EntryPoint) -> object:
        loads.append(entry.name)
        return exports[entry.value]

    monkeypatch.setattr(metadata, "entry_points", scan)
    monkeypatch.setattr(metadata.EntryPoint, "load", load)
    found = discover(
        groups=iter(("core", "partner", "alternate", "other")),
        include=lambda ep: ep.name != "excluded",
    )
    assert len(scans) == 1 and loads == ["model", "impl", "alias", "alternate"]
    models = select_variants(found, family=Message[Any], groups=["core"])
    implementations = select_implementations(found, groups=iter(("core", "partner")))
    assert models == (Query,) and implementations == (QueryObject,)
    assert select_implementations(found, groups=[], extra_implementations=[PingObject]) == (
        PingObject,
    )
    assert select_implementations(found, groups=None) == (QueryObject, Alternative)
    runtime = assemble(
        variants=models, implementations=implementations, required_interfaces=[Describable]
    )
    assert (
        runtime.adapt(Query(identifier="q", expression="discovered"), Describable).describe()
        == "discovered"
    )
    with pytest.raises(TypeError, match="single string"):
        discover(groups="core")
    with pytest.raises(TypeError, match="single string"):
        select_implementations(found, groups="core")
    assert len(scans) == 1


def test_discovery_errors_keep_source_and_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = metadata.EntryPoint(name="broken", value="broken:Broken", group="test")
    monkeypatch.setattr(metadata, "entry_points", lambda: metadata.EntryPoints([entry]))
    failure = ImportError("dependency unavailable")

    def load(entry: metadata.EntryPoint) -> object:
        raise failure

    monkeypatch.setattr(metadata.EntryPoint, "load", load)
    with pytest.raises(ImportError) as captured:
        discover(groups=["test"])
    assert captured.value is failure
    assert "broken:Broken" in failure.__notes__[-1]

    def invalid(entry: metadata.EntryPoint) -> object:
        return 42

    monkeypatch.setattr(metadata.EntryPoint, "load", invalid)
    with pytest.raises(TypeError, match="Implementation class"):
        discover(groups=["test"])


def test_unrelated_protocol_does_not_require_a_model_family() -> None:
    class Closeable(Protocol):
        @abstractmethod
        def close(self) -> None: ...

    class ResourceQuery(QueryObject, Closeable):
        def close(self) -> None:
            pass

    runtime = assemble(
        variants=[Query], implementations=[ResourceQuery], required_interfaces=[Describable]
    )
    assert (
        runtime.adapt(Query(identifier="q", expression="resource"), Describable).describe()
        == "resource"
    )
    with pytest.raises(TypeError, match="read-only model"):
        assemble(variants=[Query], implementations=[ResourceQuery], required_interfaces=[Closeable])


def test_annotation_only_model_member_is_rejected_during_assembly() -> None:
    class WritableModel(Protocol):
        model: Message[Any]

    # This mimics a declaration from an untyped plugin. Native Protocol member
    # introspection must see annotation-only members, even without an attribute.
    implementation = cast(
        type[Implementation[Any]],
        type(
            "WritableQuery",
            (QueryObject, WritableModel),
            {},
        ),
    )
    with pytest.raises(TypeError, match="read-only model"):
        assemble(variants=[Query], implementations=[implementation])
