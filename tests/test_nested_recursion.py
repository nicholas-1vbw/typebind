"""Explicit recursive bindings whose variants also reach automatically bound families."""

from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import ValidationError

from typebind import DiscriminatedModel, bind_variants, build_adapter, model, rebuild_bindings


@model(tag="provider")
class ModelConfig(DiscriminatedModel):
    model_id: str


@model(variant="openai")
class OpenAIModelConfig(ModelConfig):
    reasoning_effort: str | None = None


OPENAI = {"provider": "openai", "model_id": "m", "reasoning_effort": "low"}


# Documented recursion, extended with an automatically bound family.


@model(tag="kind")
class Node(DiscriminatedModel):
    children: list[Annotated[Node, NODE_VARIANTS]] = []
    setting: ModelConfig | None = None


@model(variant="a")
class NodeA(Node):
    value: int = 0


@model(variant="b")
class NodeB(Node):
    text: str = ""


NODE_VARIANTS = bind_variants([NodeA, NodeB, OpenAIModelConfig])
NodeA.model_rebuild()
NodeB.model_rebuild()

TREE = {
    "kind": "a",
    "children": [{"kind": "b", "setting": OPENAI, "children": [{"kind": "a", "value": 2}]}],
}


def test_recursive_binding_with_nested_family() -> None:
    with pytest.raises(ValidationError) as error:
        NodeA.model_validate(TREE)
    assert [item["type"] for item in error.value.errors()] == ["typebind_unassembled"]

    rebuild_bindings(NodeA, NodeB)
    tree = NodeA.model_validate(TREE)
    child = tree.children[0]
    assert type(child) is NodeB and type(child.setting) is OpenAIModelConfig
    assert type(child.children[0]) is NodeA

    adapter = build_adapter(Node, variants=[NodeA, NodeB, OpenAIModelConfig])
    value = adapter.validate_python(TREE)
    assert type(value.children[0].setting) is OpenAIModelConfig
    assert adapter.validate_json(adapter.dump_json(value, warnings="error")) == value
