"""Discover published models once, then select explicit family and source scopes."""

from collections.abc import Callable, Iterable, Iterator
from importlib import metadata
from itertools import chain
from typing import Any, NamedTuple, cast

from ._behavior import Implementation
from ._models import DiscriminatedModel, family_base


class DiscoveredVariant(NamedTuple):
    """A loaded model and its native publication metadata, without derived tags."""

    model: type[DiscriminatedModel]
    entry_point: metadata.EntryPoint


def _source(entry: metadata.EntryPoint) -> str:
    """Describe an entry point without losing its providing distribution."""
    distribution = entry.dist.metadata["Name"] if entry.dist is not None else "<unknown>"
    return (
        f"distribution={distribution!r}, group={entry.group!r}, "
        f"name={entry.name!r}, value={entry.value!r}"
    )


def _group_names(groups: Iterable[str]) -> frozenset[str]:
    """Consume a source scope once, rejecting accidental character scopes."""
    if isinstance(groups, str):
        raise TypeError(
            "groups must be an iterable of group names, not a single string; use {name}"
        )
    return frozenset(groups)


def discover_variants(
    *,
    groups: Iterable[str],
    include: Callable[[metadata.EntryPoint], bool] | None = None,
) -> tuple[DiscoveredVariant, ...]:
    """Load model classes from selected installed entry point groups in one batch.

    Enumerate metadata once; load each selected entry record once. ``include``
    filters native metadata before loading, so excluded entries are not imported.
    Groups may mix families. Preserve every source record, even when multiple
    records publish the same class or use the same entry name. No global cache,
    subclass scanning, instantiation, or tag validation is performed here.

    Loading failures retain their original exception and gain a source-context
    note; non-model exports raise TypeError. Predicate errors propagate unchanged.
    Groups are consumed once; a single string raises TypeError before discovery.
    Empty or absent groups yield an empty tuple. Loading executes plugin Python
    code; a later selection cannot undo import side effects.
    """
    discovered: list[DiscoveredVariant] = []
    for model, entry in _load_entries(groups, include, label="Model"):
        if not isinstance(model, type) or not issubclass(model, DiscriminatedModel):
            raise TypeError(
                f"Model entry point must export a DiscriminatedModel class ({_source(entry)}); "
                f"got {model!r}"
            )
        discovered.append(DiscoveredVariant(model, entry))
    return tuple(discovered)


class DiscoveredImplementation(NamedTuple):
    """A behavior implementation and its unmodified publication metadata."""

    implementation: type[Implementation[Any]]
    entry_point: metadata.EntryPoint


def _load_entries(
    groups: Iterable[str],
    include: Callable[[metadata.EntryPoint], bool] | None,
    *,
    label: str,
) -> Iterator[tuple[object, metadata.EntryPoint]]:
    selected_groups = _group_names(groups)
    if not selected_groups:
        return
    for entry in metadata.entry_points():
        if entry.group not in selected_groups or (include is not None and not include(entry)):
            continue
        try:
            value: object = entry.load()
        except Exception as error:
            error.add_note(f"{label} entry point: {_source(entry)}")
            raise
        yield value, entry


def discover(
    *,
    groups: Iterable[str],
    include: Callable[[metadata.EntryPoint], bool] | None = None,
) -> tuple[DiscoveredVariant | DiscoveredImplementation, ...]:
    """Discover models and behavior implementations together in one metadata scan.

    Groups may mix export kinds and families. Source filters run before loading.
    Preserve all publications for subsequent scope selection, including duplicate
    class exports. Loading failures keep their identity and gain a source note.
    Declaration reflection, family checks, conflicts and coverage belong to
    assembly, not discovery. Use discover_variants for model-only consumers.
    """
    found: list[DiscoveredVariant | DiscoveredImplementation] = []
    for value, entry in _load_entries(groups, include, label="Plugin"):
        if isinstance(value, type) and issubclass(value, DiscriminatedModel):
            found.append(DiscoveredVariant(value, entry))
        elif isinstance(value, type) and issubclass(value, Implementation):
            found.append(DiscoveredImplementation(cast(type[Implementation[Any]], value), entry))
        else:
            raise TypeError(
                f"Plugin entry point must export a DiscriminatedModel or Implementation class "
                f"({_source(entry)}); got {value!r}"
            )
    return tuple(found)


def select_implementations(
    discovered: Iterable[DiscoveredVariant | DiscoveredImplementation],
    *,
    groups: Iterable[str] | None = None,
    extra_implementations: Iterable[type[Implementation[Any]]] = (),
) -> tuple[type[Implementation[Any]], ...]:
    """Select behavior sources using the same group scope semantics as models.

    Extras bypass source filters. Deduplicate class identities after filtering.
    Target-model and interface-family resolution happen only during assembly,
    where the selected variants define the data scope.
    """
    selected_groups = None if groups is None else _group_names(groups)
    published = (
        item.implementation
        for item in discovered
        if isinstance(item, DiscoveredImplementation)
        and (selected_groups is None or item.entry_point.group in selected_groups)
    )
    selected: dict[int, type[Implementation[Any]]] = {}
    for implementation in chain(published, extra_implementations):
        selected.setdefault(id(implementation), implementation)
    return tuple(selected.values())


def select_variants(
    discovered: Iterable[DiscoveredVariant | DiscoveredImplementation],
    *,
    family: type[DiscriminatedModel],
    groups: Iterable[str] | None = None,
    extra_variants: Iterable[type[DiscriminatedModel]] = (),
) -> tuple[type[DiscriminatedModel], ...]:
    """Select model descendants from published and explicitly supplied types.

    ``groups=None`` accepts all discovered sources; an empty iterable accepts
    none. A single string raises TypeError; other iterables are consumed once.
    Source filtering does not apply to ``extra_variants``. Both sources
    are filtered by native family ancestry and deduplicated by class identity.
    Open views such as Family[Any] resolve to their native generic origin; this
    is a family query, not a claim of static assignability to Family[str]. Named
    classes and closed specializations keep their native issubclass constraints;
    DiscriminatedModel selects every model. An open discriminator is not required.

    Preserve first occurrence order, without giving it conflict precedence.
    No metadata is read or plugins loaded. Empty matches are valid; tagged-leaf
    and duplicate-tag checks belong to bind_variants/build_adapter. The input
    snapshot and model declarations remain unchanged.
    """
    base = family_base(family)
    selected_groups = None if groups is None else _group_names(groups)
    published = (
        item.model
        for item in discovered
        if isinstance(item, DiscoveredVariant)
        and (selected_groups is None or item.entry_point.group in selected_groups)
    )
    selected: dict[int, type[DiscriminatedModel]] = {}
    for model in chain(published, extra_variants):
        if issubclass(model, base):
            selected.setdefault(id(model), model)
    return tuple(selected.values())
