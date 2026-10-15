"""Types and implementations for the installed discovery example."""

from typing import Literal

from typebind_example_core import Describable, Executable, Request

from typebind import Implementation


class Write(Request[Literal["write"]]):
    value: int


class WriteObject(Implementation[Write], Describable, Executable):
    def describe(self) -> str:
        return str(self.model.value)

    def execute(self) -> str:
        return self.model.identifier
