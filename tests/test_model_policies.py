"""Verify native and identity export policies share routing without runtime switches."""

import copy
import sys
from typing import Annotated, Any, Literal

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from typebind import (
    DiscriminatedModel,
    IdentityDiscriminatedModel,
    bind_variants,
    build_adapter,
    tag_field,
)

type QueryTag = Literal["query"]


class NativeMessage[Kind: str](DiscriminatedModel):
    kind: Kind = tag_field()


class NativeRequest[Method: str](NativeMessage[Literal["request"]]):
    method: Method = tag_field()


class NativeQuery(NativeRequest[QueryTag]):
    text: str
    note: str = "default"


class IdentityMessage[Kind: str](IdentityDiscriminatedModel):
    kind: Kind = tag_field()


class IdentityRequest[Method: str](IdentityMessage[Literal["request"]]):
    method: Method = tag_field()


class IdentityQuery(IdentityRequest[QueryTag]):
    text: str
    note: str = "default"


def test_native_schema_has_no_marking_callback() -> None:
    # With no user validators, the native policy compiles straight to model-fields.
    schema = TypeAdapter(NativeQuery).core_schema
    assert schema["type"] == "model"
    assert schema["schema"]["type"] == "model-fields"
    assert NativeQuery(text="x").model_fields_set == {"text"}
    assert IdentityQuery(text="x").model_fields_set == {"kind", "method", "text"}


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("rebuild", [False, True])
def test_both_export_policies_preserve_native_defaults_and_nested_routing(
    explicit: bool, rebuild: bool
) -> None:
    native_adapter = build_adapter(NativeMessage[Any], variants=[NativeQuery])
    identity_adapter = build_adapter(IdentityMessage[Any], variants=[IdentityQuery])
    native_binding = bind_variants([NativeQuery])
    identity_binding = bind_variants([IdentityQuery])

    class Envelope(BaseModel):
        native: list[Annotated[NativeMessage[Any], native_binding]]
        identity: dict[str, Annotated[IdentityMessage[Any], identity_binding]]

    if rebuild:
        assert NativeQuery.model_rebuild(force=True)
        assert IdentityQuery.model_rebuild(force=True)
        assert native_adapter.rebuild(force=True)
        assert identity_adapter.rebuild(force=True)
        assert Envelope.model_rebuild(force=True)
    data = {"text": "x"}
    if explicit:
        data.update(kind="request", method="query")
    native = NativeQuery.model_validate(data)
    identity = IdentityQuery.model_validate(data)
    assert native.model_dump() == identity.model_dump()
    assert ("kind" in native.model_dump(exclude_unset=True)) is explicit
    assert "kind" in identity.model_dump(exclude_unset=True)
    assert "note" not in identity.model_dump(exclude_unset=True)
    for value in (native, identity):
        assert "kind" not in value.model_dump(exclude_defaults=True)
        assert "kind" not in value.model_dump(exclude={"kind"})
    assert native_adapter.validate_json(native_adapter.dump_json(native)) == native
    assert (
        identity_adapter.validate_json(identity_adapter.dump_json(identity, exclude_unset=True))
        == identity
    )
    envelope = Envelope(native=[native], identity={"x": identity})
    assert Envelope.model_validate_json(envelope.model_dump_json()) == envelope
    unset_wire = envelope.model_dump_json(exclude_unset=True)
    if explicit:
        assert Envelope.model_validate_json(unset_wire) == envelope
    else:
        with pytest.raises(ValidationError, match="union_tag_not_found"):
            Envelope.model_validate_json(unset_wire)


@pytest.mark.parametrize("model_type", [NativeQuery, IdentityQuery])
@pytest.mark.parametrize("deep", [False, True])
def test_copy_rejects_all_bound_tag_updates(
    model_type: type[NativeQuery] | type[IdentityQuery], deep: bool
) -> None:
    value = model_type(text="original")
    before = value.model_dump()
    fields_set = value.model_fields_set.copy()
    for name, expected in (("kind", "request"), ("method", "query")):
        # Both equality and retagging are rejected; mixing in payload edits is atomic.
        for tag in (expected, "wrong"):
            with pytest.raises(ValueError, match=rf"{name}.*cannot update a bound discriminator"):
                value.model_copy(update={name: tag, "text": "changed"}, deep=deep)
            with pytest.raises(ValidationError, match="frozen_field"):
                setattr(value, name, tag)
    assert value.model_dump() == before
    assert value.model_fields_set == fields_set
    for update in (None, {}, {"text": "changed"}):
        copied = value.model_copy(update=update, deep=deep)
        assert type(copied) is model_type
        assert copied is not value
        assert copied.kind == "request" and copied.method == "query"
        assert model_type.model_validate_json(copied.model_dump_json()) == copied


def test_copy_preserves_native_payload_and_extra_semantics() -> None:
    class Payload(NativeQuery, extra="allow"):
        items: list[int]

    value = Payload.model_validate({"text": "x", "items": [1], "extra": [2]})
    shallow = value.model_copy()
    deep = value.model_copy(deep=True)
    assert shallow.items is value.items
    assert deep.items == value.items and deep.items is not value.items
    assert shallow.model_extra is not value.model_extra
    assert deep.model_extra == value.model_extra
    assert deep.model_fields_set == value.model_fields_set
    updated = value.model_copy(update={"text": 123, "extra": [3]}, deep=True)
    # Only identity is protected: payload values remain unchecked, like BaseModel.
    assert vars(updated)["text"] == 123
    assert updated.model_extra == {"extra": [3]}
    assert updated.items == value.items and updated.items is not value.items
    assert value.text == "x" and value.model_extra == {"extra": [2]}


def test_copy_only_protects_bound_fields_of_open_views() -> None:
    value = NativeRequest[Any](method="open")
    assert value.model_copy(update={"method": "another"}).method == "another"
    with pytest.raises(ValueError, match="kind.*bound discriminator"):
        value.model_copy(update={"kind": "request"})


def test_deferred_copy_protection_is_ready_before_schema_build() -> None:
    class Deferred(NativeRequest[Literal["query"]], defer_build=True):
        text: str

    assert not Deferred.__pydantic_complete__
    # Even bypassing validation does not bypass model_copy's identity check.
    value = Deferred.model_construct(kind="request", method="query", text="x")
    with pytest.raises(ValueError, match="method.*bound discriminator"):
        value.model_copy(update={"method": "wrong"})
    assert not Deferred.__pydantic_complete__
    assert Deferred.model_rebuild(force=True)
    with pytest.raises(ValueError, match="kind.*bound discriminator"):
        value.model_copy(update={"kind": "wrong"})


if sys.version_info >= (3, 13):

    @pytest.mark.parametrize("model_type", [NativeQuery, IdentityQuery])
    def test_copy_replace_uses_model_copy_protection(
        model_type: type[NativeQuery] | type[IdentityQuery],
    ) -> None:
        value = model_type(text="original")
        copied = copy.replace(value, text="changed")
        assert type(copied) is model_type
        assert copied is not value and copied.text == "changed"
        assert copied.kind == "request" and copied.method == "query"
        assert model_type.model_validate_json(copied.model_dump_json()) == copied
        for name, tag in (("kind", "request"), ("method", "query")):
            for replacement in (tag, "wrong"):
                with pytest.raises(ValueError, match=rf"{name}.*bound discriminator"):
                    copy.replace(value, **{name: replacement, "text": "rejected"})
        assert value.text == "original"
        assert value.kind == "request" and value.method == "query"
