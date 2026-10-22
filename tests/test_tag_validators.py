"""Check naming rules and their ownership across native and decorated families."""

import re
from collections.abc import Callable
from typing import Any, Literal, assert_type

import pytest
from pydantic import ValidationError, create_model

from typebind import (
    DiscriminatedModel,
    IdentityDiscriminatedModel,
    build_adapter,
    model,
    tag_field,
    validate_camel_case,
    validate_kebab_case,
    validate_pascal_case,
    validate_regex,
    validate_snake_case,
)


@pytest.mark.parametrize(
    "validator, valid, invalid",
    [
        (
            validate_kebab_case,
            ("a", "query", "query2", "query-v2", "v2-3", "user-created"),
            ("User", "userCreated", "user_created", "-user", "user-", "user--created"),
        ),
        (
            validate_snake_case,
            ("a", "query", "query2", "query_v2", "v2_3", "user_created"),
            ("User", "userCreated", "user-created", "_user", "user_", "user__created"),
        ),
        (
            validate_camel_case,
            ("a", "query", "query2", "queryV2", "userCreated", "userID", "aB"),
            ("User", "USER", "UserCreated", "user_created", "user-created"),
        ),
        (
            validate_pascal_case,
            ("A", "Query", "Query2", "QueryV2", "UserCreated", "HTTPServer", "ID"),
            ("user", "userCreated", "User_Created", "User-Created"),
        ),
    ],
)
def test_builtin_naming_rules(
    validator: Callable[[str], None], valid: tuple[str, ...], invalid: tuple[str, ...]
) -> None:
    for value in valid:
        assert validator(value) is None
    for value in (*invalid, "", " ", "1User", "user created", "Query\n", "query\n", "é", "用户"):
        with pytest.raises(ValueError, match="expected ASCII"):
            validator(value)


@pytest.mark.parametrize(
    "pattern, valid, invalid",
    [
        (
            r"cmd-[a-z]+",
            "cmd-query",
            ("query", "prefix-cmd-query", "cmd-query-extra", "cmd-query\n"),
        ),
        (r"/a/g", "/a/g", ("a", "/a/g/")),
    ],
)
def test_regex_factory_requires_a_full_match(
    pattern: str, valid: str, invalid: tuple[str, ...]
) -> None:
    validator = validate_regex(pattern)
    assert_type(validator, Callable[[str], None])
    assert validator(valid) is None
    for value in invalid:
        with pytest.raises(ValueError, match="expected a full match for regex") as error:
            validator(value)
        assert repr(pattern) in str(error.value)


def test_regex_factory_reports_invalid_patterns_when_created() -> None:
    with pytest.raises(re.error):
        validate_regex(r"[")


def test_regex_flags_work_with_both_declarations_without_normalizing_values() -> None:
    validate_event = validate_regex(r"event\.[a-z]+", flags=re.IGNORECASE)

    @model(tag="kind", validator=validate_event)
    class Event(DiscriminatedModel):
        pass

    @model(variant="EVENT.created")
    class Created(Event):
        pass

    class TypedEvent[Kind: str](DiscriminatedModel):
        kind: Kind = tag_field(validator=validate_event)

    class TypedCreated(TypedEvent[Literal["EVENT.created"]]):
        pass

    assert Created().model_dump() == TypedCreated().model_dump() == {"kind": "EVENT.created"}
    with pytest.raises(ValueError, match="kind: invalid variant 'event_created'.*full match"):
        model(variant="event_created")(Event)
    with pytest.raises(ValueError, match="kind: invalid variant 'event_created'.*full match"):
        TypedEvent[Literal["event_created"]]


@pytest.mark.parametrize("defer_build", [False, True])
def test_decorated_families_validate_each_level_at_declaration(defer_build: bool) -> None:
    # Runtime field names remain independent of variant naming rules.
    @model(tag="event-kind", validator=validate_pascal_case)
    class Event(DiscriminatedModel, defer_build=defer_build):
        pass

    @model(tag="method", variant="UserEvent", validator=validate_snake_case)
    class UserEvent(Event):
        pass

    @model(variant="user_created")
    class Created(UserEvent):
        identifier: str

    value = Created(identifier="u1")
    assert value.model_dump() == {
        "event-kind": "UserEvent",
        "method": "user_created",
        "identifier": "u1",
    }
    adapter = build_adapter(Event, variants=[Created])
    assert type(adapter.validate_json(adapter.dump_json(value))) is Created
    assert Created.model_json_schema()["properties"]["method"]["const"] == "user_created"

    with pytest.raises(ValueError, match="event-kind: invalid variant 'user_event'"):
        model(variant="user_event")(Event)
    with pytest.raises(ValueError, match="method: invalid variant 'UserCreated'"):
        model(variant="UserCreated")(UserEvent)


@pytest.mark.parametrize("defer_build", [False, True])
def test_generic_families_validate_specializations_and_aliases(defer_build: bool) -> None:
    class Event[Kind: str](IdentityDiscriminatedModel, defer_build=defer_build):
        kind: Kind = tag_field(validator=validate_kebab_case)

    class Command[Method: str](Event[Literal["command-event"]]):
        method: Method = tag_field(validator=validate_camel_case)

    CreateUser = Command[Literal["createUser"]]
    value = CreateUser()
    assert_type(value.kind, Literal["command-event"])
    assert_type(value.method, Literal["createUser"])
    assert value.model_dump(exclude_unset=True) == {
        "kind": "command-event",
        "method": "createUser",
    }
    adapter = build_adapter(Event[Any], variants=[CreateUser])
    assert type(adapter.validate_json(adapter.dump_json(value))) is CreateUser

    with pytest.raises(ValueError, match="kind: invalid variant 'CommandEvent'"):
        Event[Literal["CommandEvent"]]
    with pytest.raises(ValueError, match="method: invalid variant 'create_user'"):
        Command[Literal["create_user"]]


def test_custom_method_composes_rules_and_preserves_error_context() -> None:
    class CommandNames:
        def validate(self, value: str) -> None:
            validate_kebab_case(value)
            if not value.startswith("cmd-"):
                raise ValueError("variant must start with 'cmd-'")

    names = CommandNames()

    @model(tag="kind", validator=names.validate)
    class Command(DiscriminatedModel):
        pass

    @model(variant="cmd-query")
    class Query(Command):
        pass

    assert Query().model_dump() == {"kind": "cmd-query"}
    with pytest.raises(
        ValueError, match="invalid variant 'query'.*must start with 'cmd-'"
    ) as error:
        model(variant="query")(Command)
    assert "Command.kind:" in str(error.value)
    assert isinstance(error.value.__cause__, ValueError)
    assert str(error.value.__cause__) == "variant must start with 'cmd-'"


def test_validators_run_during_declaration_and_rebuild_not_instance_validation() -> None:
    calls: list[str] = []

    def validate(value: str) -> None:
        calls.append(value)
        validate_kebab_case(value)

    @model(tag="kind", validator=validate)
    class Decorated(DiscriminatedModel):
        pass

    @model(variant="test-event")
    class DecoratedLeaf(Decorated):
        pass

    class Generic[Kind: str](DiscriminatedModel):
        kind: Kind = tag_field(validator=validate)

    class GenericLeaf(Generic[Literal["test-event"]]):
        pass

    assert calls and set(calls) == {"test-event"}
    for family, leaf in ((Decorated, DecoratedLeaf), (Generic[Any], GenericLeaf)):
        adapter = build_adapter(family, variants=[leaf])
        calls.clear()
        for data in ({}, {"kind": "test-event"}):
            value = leaf.model_validate(data)
            assert value.model_dump() == {"kind": "test-event"}
            assert adapter.validate_json(adapter.dump_json(value)) == value
            assert leaf.model_validate_json(value.model_dump_json()) == value
        with pytest.raises(ValidationError, match="literal_error"):
            leaf.model_validate({"kind": "other-event"})
        assert calls == []
        leaf.model_rebuild(force=True)
        assert calls and set(calls) == {"test-event"}


@pytest.mark.parametrize(
    "inherited, replacement",
    [
        (validate_kebab_case, None),
        (validate_kebab_case, validate_snake_case),
        (None, validate_kebab_case),
    ],
)
def test_subclasses_cannot_replace_the_owning_familys_validator(
    inherited: Callable[[str], None] | None, replacement: Callable[[str], None] | None
) -> None:
    @model(tag="kind", validator=inherited)
    class Event(DiscriminatedModel):
        pass

    with pytest.raises(TypeError, match="cannot override the inherited tag validator"):
        create_model(
            "ChangedValidator",
            __base__=Event,
            kind=(Literal["query"], tag_field(validator=replacement)),
        )


@pytest.mark.parametrize("value", ["with spaces", "camelCase", "snake_case", "用户"])
def test_families_without_validators_keep_accepting_nonempty_strings(value: str) -> None:
    @model(tag="kind")
    class Event(DiscriminatedModel):
        pass

    leaf = model(variant=value)(Event)
    assert leaf().model_dump()["kind"] == value


@pytest.mark.parametrize("validator", ["CamelCase", 42, False])
def test_noncallable_validators_are_rejected(validator: Any) -> None:
    with pytest.raises(TypeError, match="validator must be a callable"):
        tag_field(validator=validator)
    with pytest.raises(TypeError, match="validator must be a callable"):
        model(tag="kind", validator=validator)(DiscriminatedModel)


@pytest.mark.parametrize("variant", [None, "query"])
def test_decorator_validator_requires_a_new_tag(variant: str | None) -> None:
    with pytest.raises(ValueError, match="validator requires tag="):
        model(variant=variant, validator=validate_kebab_case)


@pytest.mark.parametrize("result", [False, True, "rewritten"])
def test_validators_cannot_return_predicates_or_replacement_values(result: Any) -> None:
    def invalid(value: str) -> Any:
        return result

    class Event[Kind: str](DiscriminatedModel):
        kind: Kind = tag_field(validator=invalid)

    with pytest.raises(TypeError, match="must return None or raise ValueError"):
        Event[Literal["query"]]
