# typebind

Build extensible Pydantic model families with automatic discriminator tags and
statically typed fields. Bind behavior implementations to models without mixing
them into the data classes. Select variants explicitly, or optionally discover
models and implementations supplied by installed plugin packages.

## Install

Python 3.12 or later is required. This project is preparing its first release and
has not been published to PyPI. From a local checkout:

```sh
python -m pip install .
```

The supported Pydantic range and other runtime requirements are declared in
[pyproject.toml](https://github.com/nicholas-1vbw/typebind/blob/main/pyproject.toml).

## Quick start

Declare a family and its variants, then build an adapter for the variants you accept:

```python
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
assert adapter.validate_json(wire) == query
assert type(adapter.validate_python({"kind": "ping", "identifier": "p"})) is Ping
print(wire.decode())  # {"kind":"query","identifier":"q","expression":"hello"}
```

`build_adapter` returns a native Pydantic `TypeAdapter`. `Message[Any]` keeps
common fields typed while allowing runtime-selected tags; concrete variants
retain their `Literal` tags. For nested model fields, use
[`bind_variants`](https://github.com/nicholas-1vbw/typebind/blob/main/docs/models.md#consumer-types).
Import public APIs from `typebind`; underscore-prefixed modules are internal.

## Usage notes

- Constructors can omit fixed tags, but incoming data routed through an adapter
  must contain them. Keep tags in persisted JSON and bind the reader's accepted variants.
- Ordinary dumps retain tags. If you need `exclude_unset=True`, use
  `IdentityDiscriminatedModel` to retain omitted tags. Explicit tag exclusions and
  `exclude_defaults=True` can still remove them; see the
  [serialization contract](https://github.com/nicholas-1vbw/typebind/blob/main/docs/models.md#json-serialization-contract).
- Explicit generics provide static tag types. The alternative
  [`@model` decorator](https://github.com/nicholas-1vbw/typebind/blob/main/docs/models.md#decorator-declarations)
  reduces declaration boilerplate, but generated tag attributes are runtime-only.
- Strict Pyright is verified. Discriminator aliases, additional payload type
  parameters and multiple model-family parents are unsupported; see
  [typing and compatibility boundaries](https://github.com/nicholas-1vbw/typebind/blob/main/docs/models.md#current-boundaries).

## Documentation and examples

- [Runnable examples](https://github.com/nicholas-1vbw/typebind/blob/main/examples/README.md): setup and commands for model families, manual behavior assembly, decorators and cross-package discovery.
- [Models](https://github.com/nicholas-1vbw/typebind/blob/main/docs/models.md): declarations, nested binding, serialization and copying.
- [Behaviors](https://github.com/nicholas-1vbw/typebind/blob/main/docs/behaviors.md): typed interfaces, assembly and dispatch contracts.
- [Discovery](https://github.com/nicholas-1vbw/typebind/blob/main/docs/discovery.md): package layout, entry points and scoped selection. Discovery is optional for all model and behavior APIs.

## Contributing

See [Contributing](https://github.com/nicholas-1vbw/typebind/blob/main/CONTRIBUTING.md)
for development setup and checks, and
[Releasing](https://github.com/nicholas-1vbw/typebind/blob/main/docs/releasing.md)
for publishing. Report vulnerabilities through the
[security policy](https://github.com/nicholas-1vbw/typebind/blob/main/SECURITY.md).

## License

[MIT](https://github.com/nicholas-1vbw/typebind/blob/main/LICENSE).
