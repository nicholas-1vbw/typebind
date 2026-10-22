"""Explicit, hierarchical discriminated models backed by Pydantic."""

from ._behavior import BehaviorAdapter, Implementation, assemble
from ._discovery import (
    DiscoveredImplementation,
    DiscoveredVariant,
    discover,
    discover_variants,
    select_implementations,
    select_variants,
)
from ._models import (
    DiscriminatedModel,
    IdentityDiscriminatedModel,
    bind_variants,
    build_adapter,
    model,
    tag_field,
)
from ._validators import (
    validate_camel_case,
    validate_kebab_case,
    validate_pascal_case,
    validate_snake_case,
)

__all__ = [
    "BehaviorAdapter",
    "Implementation",
    "assemble",
    "DiscoveredImplementation",
    "discover",
    "select_implementations",
    "DiscoveredVariant",
    "DiscriminatedModel",
    "IdentityDiscriminatedModel",
    "bind_variants",
    "build_adapter",
    "discover_variants",
    "select_variants",
    "tag_field",
    "model",
    "validate_camel_case",
    "validate_kebab_case",
    "validate_pascal_case",
    "validate_snake_case",
]
