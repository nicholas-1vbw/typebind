"""Shared declarations for manual assembly: two models and two interfaces."""

from abc import abstractmethod
from typing import Any, Literal, Protocol

from typebind import DiscriminatedModel, Implementation, tag_field


class Command[Kind: str](DiscriminatedModel):
    kind: Kind = tag_field()
    identifier: str


class Describable(Protocol):
    @property
    @abstractmethod
    def model(self) -> Command[Any]: ...

    @abstractmethod
    def describe(self) -> str: ...


class Executable(Protocol):
    @property
    @abstractmethod
    def model(self) -> Command[Any]: ...

    @abstractmethod
    def execute(self) -> str: ...


class Uppercase(Command[Literal["uppercase"]]):
    text: str


class Add(Command[Literal["add"]]):
    left: int
    right: int


class UppercaseObject(Implementation[Uppercase], Describable, Executable):
    def describe(self) -> str:
        return f"Convert {self.model.text!r} to uppercase"

    def execute(self) -> str:
        return self.model.text.upper()


class AddObject(Implementation[Add], Describable, Executable):
    def describe(self) -> str:
        return f"Add {self.model.left} and {self.model.right}"

    def execute(self) -> str:
        return str(self.model.left + self.model.right)
