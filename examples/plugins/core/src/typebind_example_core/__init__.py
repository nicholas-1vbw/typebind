"""Types and implementations for the installed discovery example."""

from abc import abstractmethod
from typing import Any, Literal, Protocol

from typebind import DiscriminatedModel, Implementation, tag_field


class Message[Kind: str](DiscriminatedModel):
    kind: Kind = tag_field()
    identifier: str


class Request[Method: str](Message[Literal["request"]]):
    method: Method = tag_field()


class Query(Request[Literal["query"]]):
    expression: str


class Template[Kind: str](DiscriminatedModel):
    kind: Kind = tag_field()


class BasicTemplate(Template[Literal["basic"]]):
    content: str


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
