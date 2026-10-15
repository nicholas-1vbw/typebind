"""Verify native and identity export policies share routing without runtime switches."""

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


def test_native_schema_has_no_marking_callback_or_derived_names() -> None:
    # With no user validators, the native policy compiles straight to model-fields.
    schema = TypeAdapter(NativeQuery).core_schema
    assert schema["type"] == "model"
    assert schema["schema"]["type"] == "model-fields"
    assert not hasattr(NativeQuery, "_mark_bound_tags")
    assert not hasattr(NativeQuery, "_bound_tag_names")
    assert not hasattr(DiscriminatedModel, "_mark_bound_tags")
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
