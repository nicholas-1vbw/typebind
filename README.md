# typebind

Pydantic model families with automatic discriminator tags, typed behavior adapters,
and optional plugin discovery. Declare models with native generics and `Literal`;
choose their variants explicitly, or load them from installed entry points.

## Install

Python 3.14 or later is required. This project is preparing its first release and
has not been published to PyPI. From a local checkout:

```sh
python -m pip install .
```

Runtime requirements are declared in [pyproject.toml](https://github.com/nicholas-1vbw/typebind/blob/main/pyproject.toml). Development uses
`uv.lock` and the exact uv version in `tool.uv.required-version`.

## Quick start: model families

This complete example selects its variants manually. Concrete constructors can
omit fixed tags; incoming data routed through an adapter must contain them.

```python
"""Declare tagged models, select variants manually, and round-trip JSON."""

from typing import Any, Literal

from typebind import DiscriminatedModel, build_adapter, tag_field


class Message[Kind: str](DiscriminatedModel):
    kind: Kind = tag_field()
    identifier: str


class Query(Message[Literal["query"]]):
    expression: str


class Ping(Message[Literal["ping"]]):
    pass


adapter = build_adapter(Message[Any], variants=(Query, Ping))
query = Query(identifier="q", expression="hello")  # kind defaults to "query"
wire = adapter.dump_json(query)
restored = adapter.validate_json(wire)
assert type(restored) is Query
assert restored == query
assert type(adapter.validate_python({"kind": "ping", "identifier": "p"})) is Ping
print(wire.decode())
```

`build_adapter` returns a native Pydantic `TypeAdapter`. For nested containers,
use `Annotated[Family[Any], bind_variants(variants)]` as described in the
[model guide](https://github.com/nicholas-1vbw/typebind/blob/main/docs/models.md). `Family[Any]` keeps common fields typed while allowing
runtime-selected tags; concrete models retain exact `Literal` tags.

## Choose an example

From a development checkout, `uv run --locked` synchronizes the locked environment
before running each task:

| Example | Command | What it demonstrates |
| --- | --- | --- |
| [Models](https://github.com/nicholas-1vbw/typebind/blob/main/examples/models.py) | `uv run --locked poe example-models` | Automatic tags, explicit variants, JSON round trips |
| [Manual assembly](https://github.com/nicholas-1vbw/typebind/blob/main/examples/manual.py) | `uv run --locked poe example-manual` | One family, two variants, two interfaces, nested serialization and typed `adapt()` |
| [Cross-package discovery](https://github.com/nicholas-1vbw/typebind/blob/main/examples/discovery.py) | `uv run --locked poe example-discovery` | Two installed distributions, native entry points, family/scope selection and behavior assembly |

The manual example shares its declarations in [commands.py](https://github.com/nicholas-1vbw/typebind/blob/main/examples/commands.py) and
passes tuples directly to `assemble`:

```python
from commands import (
    Add,
    AddObject,
    Describable,
    Executable,
    Uppercase,
    UppercaseObject,
)
from typebind import assemble

variants = (Uppercase, Add)
runtime = assemble(
    variants=variants,
    implementations=(UppercaseObject, AddObject),
    required_interfaces=(Describable, Executable),
)
result = runtime.adapt(Add(identifier="a", left=20, right=22), Executable)
assert result.execute() == "42"
```

Discovery is optional. It obtains the classes supplied to the same assembly
functions; it is not required to declare models, bind variants, or call behaviors.
The discovery example installs real [example packages](https://github.com/nicholas-1vbw/typebind/blob/main/examples/plugins) as development
dependencies and fails when they are missing. These packages are not typebind
runtime dependencies and are not shipped inside its wheel.

## Guides and contracts

- [Models](https://github.com/nicholas-1vbw/typebind/blob/main/docs/models.md): multilevel discriminators, aliases, nested unions, duplicate tags and validation boundaries.
- [Behaviors](https://github.com/nicholas-1vbw/typebind/blob/main/docs/behaviors.md): interface/family binding, manual assembly, coverage, conflicts and exact-type dispatch.
- [Discovery](https://github.com/nicholas-1vbw/typebind/blob/main/docs/discovery.md): package layout, entry points, group/family relationships, scopes and local extras.

Each family introduces one discriminator type parameter. Discriminator aliases,
additional payload type parameters, and multiple model-family parents are not
supported. Strict Pyright is verified; PyCharm and Mypy are not. Pydantic's
supported minor series is intentionally bounded because declaration validation
reads its generic metadata; see the [model boundaries](https://github.com/nicholas-1vbw/typebind/blob/main/docs/models.md#current-boundaries).

## Development and verification

```sh
uv run --locked poe check
uv run --locked poe benchmark
```

`check` runs Ruff, strict Pyright, pytest, all three examples, a wheel/sdist build,
and artifact verification. Tests execute the README quick start and guide snippets.
The installed-plugin test builds the real example packages and checks discovery,
nested round trips and strict typing against installed wheels. The native build
creates a wheel from the sdist; artifact checks verify metadata, MIT, `py.typed`,
source contents, and clean-environment execution outside the checkout.

[CI](https://github.com/nicholas-1vbw/typebind/blob/main/.github/workflows/ci.yml) runs the same Poe sub-tasks as separate steps on Linux, macOS and Windows.
It synchronizes locked dependencies once, then uses `uv run --no-sync` for each
step. Local `poe check` remains the aggregate entry point. Its microbenchmark
has no timing threshold and does not measure service throughput. Local passing
checks do not imply that hosted CI has passed. Development tools and example
packages are in the development dependency group; the library's runtime
requirements remain separate. Update `uv.lock` when dependencies change.

## Release preparation

CI only verifies; it does not publish. Before the first release, confirm the PyPI
name and publishing permissions, run hosted CI, and configure a dedicated release
workflow with [Trusted Publishing](https://docs.pypi.org/trusted-publishers/).
A repository name does not reserve the PyPI project name.

Set the version in `pyproject.toml`, update the lockfile, run `uv run --locked poe check`,
and publish only the reviewed wheel and sdist for that version. Verify installation
from the index in a clean consumer environment after publishing. See the
[uv publishing guide](https://docs.astral.sh/uv/guides/package/).

## License

[MIT](https://github.com/nicholas-1vbw/typebind/blob/main/LICENSE).
