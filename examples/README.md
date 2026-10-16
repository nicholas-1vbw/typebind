# Examples

[Back to README](../README.md). These runnable scripts demonstrate model families,
behavior assembly and optional plugin discovery. API contracts live in the
[model](../docs/models.md), [behavior](../docs/behaviors.md) and
[discovery](../docs/discovery.md) guides.

## Run an example

Use a repository checkout and the uv version specified by `tool.uv.required-version`
in [pyproject.toml](../pyproject.toml). See [development setup](../CONTRIBUTING.md#set-up-and-run-checks)
for uv installation. Run `uv sync --locked` from the repository root to prepare
the locked development environment, then run the commands below from that directory.
Installing only `typebind` with pip does not install these scripts or their local
plugin dependencies.

| Example | Command | What it demonstrates |
| --- | --- | --- |
| [Models](models.py) | `uv run --no-sync poe example-models` | Automatic tags, explicit variants, JSON round trips and both export policies |
| [Manual assembly](manual.py) | `uv run --no-sync poe example-manual` | One family, two variants, two interfaces, nested serialization and typed `adapt()` |
| [Decorators](decorators.py) | `uv run --no-sync poe example-decorators` | Multilevel decorator declarations, typed payloads, behavior assembly and nested JSON round trips |
| [Cross-package discovery](discovery.py) | `uv run --no-sync poe example-discovery` | Two installed distributions, entry points, family selection and behavior assembly |

To run all four:

```sh
uv run --no-sync poe example
```

The scripts assert their validation and round-trip results and print JSON or
behavior output. An assertion failure or missing dependency stops the script.

## Models and manual assembly

Start with [models.py](models.py) for a self-contained model example. It compares
ordinary dumps with `exclude_unset=True` for the two base classes; the
[export policy guide](../docs/models.md#choosing-export-semantics) explains the choice.

[commands.py](commands.py) declares the `Command` family, `Uppercase` and `Add`
variants, `Describable` and `Executable` interfaces, and their implementations.
[manual.py](manual.py) imports those declarations, passes explicit tuples to
`assemble`, and calls both interfaces on models restored from a nested JSON envelope.
It needs no discovery calls or plugin publication metadata.

[decorators.py](decorators.py) is self-contained. It uses `@model` for a multilevel
family and assembles behaviors manually. See
[decorator declarations](../docs/models.md#decorator-declarations) for static tag
limitations and recursive fields.

## Cross-package discovery

The root development group installs two local example distributions through
`tool.uv.sources` path sources. They are not runtime dependencies of `typebind`
and are not included in its wheel. They share the root lockfile.

| Distribution | Contents |
| --- | --- |
| [typebind-example-core](plugins/core/pyproject.toml) | Defines the `Message` / `Request` and `Template` families and interfaces; publishes `Query`, `BasicTemplate` and `QueryObject` |
| [typebind-example-extra](plugins/extra/pyproject.toml) | Publishes `Write(Request[Literal["write"]])` and `WriteObject`, importing the shared contract from core |

The core package uses `IdentityDiscriminatedModel` for messages and
`DiscriminatedModel` for templates, demonstrating that discovery accepts both.

[discovery.py](discovery.py) makes one batch discovery call, selects both top-level
and intermediate families, assembles two interfaces, and round-trips a nested
envelope. It checks that both distributions were found and filters out the
unrelated `Template` family. It supplies no `extra_variants` or
`extra_implementations`; missing plugins fail instead of falling back to local classes.

For package layout and entry point declarations in your own project, see the
[discovery guide](../docs/discovery.md#package-layout). Installed-wheel verification
is described in [Contributing](../CONTRIBUTING.md#set-up-and-run-checks).
