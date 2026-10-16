"""Assemble typed object adapters independently of model validation and discovery."""

from collections.abc import Iterable
from inspect import isabstract, signature
from types import MappingProxyType, get_original_bases
from typing import (
    Any,
    Protocol,
    TypeVar,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)

from typing_extensions import get_protocol_members, is_protocol

from ._models import DiscriminatedModel, concrete_model, family_base


def _name(cls: type[object]) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


class Implementation[T: DiscriminatedModel]:
    """Hold a model without copying or validating it; specialize with its concrete type.

    Implement interfaces through ordinary explicit Protocol inheritance. The
    read-only property preserves precise model types without modifying the model
    class. Each adaptation creates a new implementation instance.
    """

    def __init__(self, model: T) -> None:
        self._model = model

    @property
    def model(self) -> T:
        """The original model, whose lifetime is shared with this adapter object."""
        return self._model


def _implementation_class(value: object) -> type[Implementation[Any]]:
    if not isinstance(value, type) or not issubclass(value, Implementation):
        raise TypeError(f"Expected an Implementation class; got {value!r}")
    return cast(type[Implementation[Any]], value)


def _implementation_model(implementation: type[Implementation[Any]]) -> type[DiscriminatedModel]:
    """Resolve native generic bases, including type-variable forwarding through mixins."""
    targets: set[type[DiscriminatedModel]] = set()

    def visit(cls: type[object], substitutions: dict[TypeVar, object]) -> None:
        for base in get_original_bases(cls):
            origin: object = get_origin(base) or base
            if not isinstance(origin, type):
                continue
            arguments = tuple(
                substitutions.get(arg, arg) if isinstance(arg, TypeVar) else arg
                for arg in get_args(base)
            )
            if origin is Implementation:
                if len(arguments) != 1:
                    raise TypeError(
                        f"{_name(implementation)}: expected Implementation[ConcreteModel], "
                        "not a bare base"
                    )
                targets.add(concrete_model(arguments[0]))
            elif issubclass(origin, Implementation):
                parent = cast(type[Implementation[Any]], origin)
                parameters = cast(tuple[TypeVar, ...], getattr(parent, "__parameters__", ()))
                visit(parent, dict(zip(parameters, arguments, strict=True)))

    visit(implementation, {})
    if len(targets) != 1:
        raise TypeError(
            f"{_name(implementation)}: expected exactly one concrete Implementation[Model] target"
        )
    return next(iter(targets))


def _interface_family(interface: object) -> type[DiscriminatedModel]:
    """Read the interface's native property annotation as the authoritative family."""
    if not isinstance(interface, type) or not is_protocol(interface):
        raise TypeError(f"Expected a Protocol interface; got {interface!r}")
    member: object = getattr(interface, "model", None)
    if not isinstance(member, property) or member.fget is None or member.fset is not None:
        raise TypeError(
            f"{_name(interface)}: declare a read-only model property returning a family"
        )
    try:
        family: object = get_type_hints(member.fget).get("return")
        return family_base(family)
    except Exception as error:
        error.add_note(f"Interface family: {_name(interface)}.model")
        raise


class BehaviorAdapter:
    """An assembled scope, with exact-class lookup and fresh adapter objects.

    Construct with assemble(). No model parsing, reflection, discovery, family
    checking, or fallback search occurs on the successful adapt path. Interface
    methods dispatch normally after adaptation. Unknown pairs raise LookupError.
    """

    def __init__(
        self,
        *,
        variants: Iterable[type[DiscriminatedModel]],
        implementations: Iterable[type[Implementation[Any]]],
        required_interfaces: Iterable[type[object]] = (),
    ) -> None:
        models = dict.fromkeys(concrete_model(model) for model in variants)
        families: dict[type[object], type[DiscriminatedModel]] = {}
        required = tuple(dict.fromkeys(required_interfaces))
        for interface in required:
            families[interface] = _interface_family(interface)
        factories: dict[
            tuple[type[DiscriminatedModel], type[object]], type[Implementation[Any]]
        ] = {}
        for candidate in dict.fromkeys(implementations):
            implementation = _implementation_class(candidate)
            try:
                model = _implementation_model(implementation)
                if model not in models:
                    continue
                if isabstract(implementation):
                    raise TypeError("Cannot assemble an abstract implementation")
                # Do not execute constructors during assembly. They must accept
                # one model; optional constructor dependencies remain possible.
                signature(implementation).bind(object())
                interfaces = tuple(base for base in implementation.__mro__ if is_protocol(base))
                for interface in interfaces:
                    # Only model-bound Protocols declare behavior interfaces.
                    # Ordinary orthogonal Protocols may coexist on the object.
                    if interface is Protocol or "model" not in get_protocol_members(interface):
                        continue
                    if interface not in families:
                        families[interface] = _interface_family(interface)
                    if not issubclass(model, families[interface]):
                        raise TypeError(
                            f"{_name(model)} is outside interface {_name(interface)} "
                            f"family {_name(families[interface])}"
                        )
                    key = model, interface
                    previous = factories.get(key)
                    if previous is not None and previous is not implementation:
                        raise ValueError(
                            f"Duplicate implementation for {_name(model)} / "
                            f"{_name(interface)}: {_name(previous)} and "
                            f"{_name(implementation)}"
                        )
                    factories[key] = implementation
            except Exception as error:
                error.add_note(f"Behavior implementation: {_name(implementation)}")
                raise
        for model in models:
            for interface in required:
                if issubclass(model, families[interface]) and (model, interface) not in factories:
                    raise ValueError(
                        f"Missing implementation for {_name(model)} / {_name(interface)}"
                    )
        self._factories = MappingProxyType(factories)

    def adapt[I](self, model: DiscriminatedModel, interface: type[I]) -> I:
        """Bind an existing model to an interface; preserve the static interface type.

        The heterogeneous table was checked at assembly. This local cast restores
        its dependent result type; callers never need a cast. Plugins remain
        trusted Python code, and their method signatures are checked statically.
        """
        try:
            factory = self._factories[type(model), interface]
        except KeyError:
            raise LookupError(
                f"No implementation in this scope for {_name(type(model))} / {interface!r}"
            ) from None
        return cast(I, factory(model))


def assemble(
    *,
    variants: Iterable[type[DiscriminatedModel]],
    implementations: Iterable[type[Implementation[Any]]],
    required_interfaces: Iterable[type[object]] = (),
) -> BehaviorAdapter:
    """Build a scope snapshot and validate every applicable required interface.

    Only exact targets in variants participate in dispatch/conflict/coverage
    checks; other targets can coexist in the discovered sources. Repeated class
    identities are harmless. Different implementations of the same pair fail,
    regardless of publication order. Required interfaces apply to all selected
    models in their family, including descendants of intermediate families.

    A new scope requires a new assembly. There is no global registry, automatic
    subclass fallback, object cache, or constructor execution during assembly.
    """
    return BehaviorAdapter(
        variants=variants,
        implementations=implementations,
        required_interfaces=required_interfaces,
    )
