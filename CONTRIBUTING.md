# Contributing

## Set up and run checks

Fork the repository and create a branch from `main`. Install the uv version
specified by `tool.uv.required-version` in [pyproject.toml](pyproject.toml), using
[uv's installation instructions](https://docs.astral.sh/uv/getting-started/installation/).
Do not relax that requirement to use a different local tool version.

From the checkout root:

```sh
uv sync --locked
uv run --no-sync poe check
```

uv installs the Python version selected by `.python-version` when needed. The
root development group includes the two local plugin examples. No package-index
credentials, publishing permissions or application secrets are needed.

`poe check` runs formatting, lint, strict Pyright, tests, runnable examples,
builds and isolated wheel verification. Run focused tasks while developing:

```sh
uv run --no-sync poe test
uv run --no-sync pytest -m integration
uv run --no-sync poe benchmark
```

See [Examples](examples/README.md) for individual example commands and what each
script demonstrates.

The test task reports branch coverage and missing lines as diagnostics; there is
no percentage gate. The `integration` marker selects the installed-distribution
test independently. It builds the two plugin wheels once, then checks current,
development and alpha library wheels. All three cases verify installation,
version, exact local wheel origins and imports. Only the current-version case
runs the discovery example against installed wheels, verifying real entry points,
behavior results, nested JSON round trips and cross-package Pyright inference.
Generic model and decorator wheel checks belong to artifact verification.

The build creates the wheel from the sdist. Artifact verification checks package
metadata, MIT licensing, `py.typed`, source contents and isolated runtime/typing
behavior. Installers follow wheel dependency metadata under lockfile-derived
constraints; they do not preinstall undeclared dependencies. Isolated installs
may access PyPI, so `poe check` is not an offline command. Runnable examples are
usage acceptance checks; documentation prose is reviewed, not parsed by tests.

Example packages belong to this checkout and must use its current source, not an
independently selected typebind release. Their dependency metadata declares
package relationships without version constraints. Root uv source configuration
selects the local examples during development; the integration test installs the
library and plugin wheels built from this checkout and verifies their installation
origins. The development and alpha cases change only the candidate version metadata.
Changing the main package version does not require bumping the examples' own versions.

To check the minimum supported Python on POSIX shells, run
`UV_PYTHON=3.12 uv sync --locked`, then `uv run --no-sync poe check`.
On PowerShell set `$env:UV_PYTHON = "3.12"` before running `uv sync --locked`
and `uv run --no-sync poe check`. CI checks Python 3.12–3.15 on Ubuntu 24.04. macOS and Windows are not part of
the ongoing CI validation. The full matrix runs on PRs, main pushes, manual
checks and the release workflow. Ordinary branch pushes do not trigger CI.
Each job syncs dependencies once and runs the Poe sub-tasks as separate steps.

A separate Python 3.12 job checks the minimum runtime dependencies declared in
`pyproject.toml`. It keeps the locked development tools, resolves runtime
constraints with `uv pip compile --resolution lowest-direct`, and installs that
combination before running strict typing, non-integration tests and all examples.
Artifact verification consumes the same constraints through `check-dist --constraint`,
so its isolated wheel installation also exercises the minimum dependencies.
The installed-plugin integration tests remain in the locked Python matrix; their
own isolated environments use lockfile constraints.

For workflow changes, run actionlint (the version is declared in `ci.yml`) and:

```sh
uv run --no-sync poe workflow-security
```

This runs offline zizmor checks. Validate orchestration with native tools and
actual Actions runs; do not add library tests that mirror workflow YAML or step
order. Exercise new project-owned automation behavior at its own boundary.

## Measure performance changes

Run `poe benchmark` for performance-related changes, or use
`poe benchmark-models` and `poe benchmark-behavior` separately. Benchmarks are
manual diagnostics, not a routine CI step or a timing gate.

Model cases compare omitted and explicit tags, both public bases, multilevel
families, Literal aliases and decorators against native Pydantic controls with
matching frozen fields and default validation. A native identity-marking validator
separates the cost of that guarantee from plain defaults. Copy cases isolate the
identity guard cost; JSON cases exercise serialization, validation and nested
`exclude_unset=True` round trips. Behavior cases compare table-based adaptation
with direct implementation construction. These microbenchmarks do not establish
service throughput; report the interpreter and comparison cases with results.

## Submit a pull request

Describe the concrete behavior changed, why it is needed, and the checks actually
run. Include a small reproduction for bug fixes. Add tests for observable library
behavior; keep configuration and documentation changes focused on their owners.
Keep code, examples, comments and repository documentation in English.

Call out public API, serialization, typing and compatibility changes. Preserve
field definitions as the source of tags and keep discovery, assembly and runtime
adaptation separate. The contracts and current limitations are in `docs/`.
Benchmarks report measurements; they are not machine-dependent pass/fail gates.

Keep credentials, private data, generated distributions and virtual environments
out of commits. Report vulnerabilities through [the security policy](SECURITY.md),
not public bug reports. External PRs run without publishing credentials. CI
approval does not authorize publishing. Maintainers review and merge PRs; there
is no automatic merge for dependency updates.

## Environment and dependency maintenance

The workflow files own runner labels. CI and publishing both use Ubuntu 24.04 (x64). Change OS
versions explicitly in a reviewed PR and verify all affected jobs. Versioned
labels avoid automatic OS-major migration; GitHub still updates the images and
preinstalled tools. This is not a bit-for-bit frozen VM.

The Python compatibility matrix intentionally follows current patch releases of
3.12 through 3.15. Local development and publishing select `.python-version`.
`pyproject.toml` fixes uv and the release build-backend constraint; `uv.lock`
selects development and runtime dependencies. Third-party Actions use commit
SHAs. actionlint has a fixed Go module version. Go, Git, shell utilities and the
GitHub CLI are runner-provided tooling, deliberately updated with the image;
they are not claimed to be pinned or covered by the Python lock audit.

Actions Dependabot checks weekly. Minor/patch version updates share a PR; major
updates remain separate. Security updates are not delayed by that version-update
group. Review the diff and require the full CI checks before merging.

Python Dependabot is not enabled yet: the inspected updater source
uses uv 0.12.19, while this project's required uv is 0.13.0. This source inspection
is not a successful hosted updater test. Keep the project pin; do not add a second
lockfile or weaken the toolchain to accommodate a bot. Until a hosted updater can
resolve the local example sources with the required uv, maintainers update Python
dependencies in ordinary PRs:

```sh
uv lock --upgrade-package PACKAGE_NAME
uv sync --locked
uv run --no-sync poe check
uv run --no-sync poe audit
```

For Pydantic updates, update `pydantic` and let its exact dependency select
`pydantic-core`; typebind does not impose another core version constraint.
Follow the [compatibility checks](docs/models.md#current-boundaries) for dependency
changes. The locked matrix and minimum-dependency job cover their actual resolved
combinations, not every version allowed by the package metadata.

`poe audit` exports all locked third-party runtime/development entries to a
temporary native `pylock.toml`, then uses pip-audit's lock reader with strict
collection. The independent `Dependency audit` workflow runs weekly or manually.
Vulnerabilities, collection failures and service errors fail that workflow and
remain visible in Actions notifications; they are not suppressed. It is not a
required PR check or a dependency of the publishing workflow. Maintainers triage
findings for affected code paths and environments, and review new dependency risks
when updating dependencies. A failed advisory query is not a vulnerability finding.
An unresolved risk relevant to the release must be addressed before approving its
upload; an unrelated development-dependency advisory is not an automatic release veto.
The audit does not cover local project code, Actions, runner tools or the separate
build-backend constraint. Review backend advisories when upgrading the toolchain.
Security fixes still require review and tests; the audit does not update packages.
Run it from Actions → Dependency audit → Run workflow, selecting `main`, or use
`gh workflow run audit.yml --ref main`. The workflow must exist on the default
branch for dispatch and scheduling. Inspect its first scheduled result and
notification delivery on GitHub; local audit success does not establish either.

Before enabling Python Dependabot, verify a real hosted update that changes a
resolved dependency and passes CI with the required uv. A no-op lock rewrite is
not that acceptance test. See [uv's Dependabot guide](https://docs.astral.sh/uv/guides/integration/dependabot/)
and the [updater source](https://github.com/dependabot/dependabot-core/blob/780c17e16ac2cd5b8e04a9e27fc2da4de6043290/uv/Dockerfile).

Publishing and one-time owner settings are described in [Releasing](docs/releasing.md).
