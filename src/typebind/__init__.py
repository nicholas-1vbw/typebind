"""Explicit, hierarchical discriminated models backed by Pydantic."""

from ._core import DiscriminatedModel, bind_variants, build_adapter, tag_field
from .behavior import BehaviorAdapter, Implementation, assemble
from .discovery import (
    DiscoveredImplementation,
    DiscoveredVariant,
    discover,
    discover_variants,
    select_implementations,
    select_variants,
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
    "bind_variants",
    "build_adapter",
    "discover_variants",
    "select_variants",
    "tag_field",
]
