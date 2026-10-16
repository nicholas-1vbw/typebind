# Releasing

Releases use standard wheel/sdist artifacts and the official
[`pypa/gh-action-pypi-publish`](https://github.com/pypa/gh-action-pypi-publish)
action with Trusted Publishing. No PyPI API token, `.pypirc`, or GitHub secret is
needed. GitHub issues a short-lived OIDC identity to each upload job; the index
checks that it matches the configured repository, workflow and environment.

The [Publish workflow](https://github.com/nicholas-1vbw/typebind/blob/main/.github/workflows/publish.yml)
starts production automatically when a `v*` tag is pushed. It runs full checks,
verifies the tag, version and main ancestry, builds distributions, waits for the
configured `pypi` approval, uploads to PyPI, verifies installation, then creates
GitHub Release notes and attaches the same distributions. Approval is enforced
by GitHub environment settings, not by the presence of `environment` in YAML.

| Trigger | Path after shared CI, build and artifact validation |
| --- | --- |
| Manual `check` (default) | Stop; no upload or publishing environment |
| Manual `testpypi` on `main` | TestPyPI approval, upload and installed-wheel verification; no version tag |
| Push a protected `v*` tag | Production approval, PyPI upload, installed-wheel verification, GitHub Release |

There is no manual production target. Merging a PR, pushing a branch, or creating
a GitHub Release does not trigger publication. Ordinary CI never uploads.
GitHub requires repository write access for manual dispatch; tag creation and
production approval have separate controls described below.

TestPyPI is an independent, optional rehearsal. Production does not depend on
its availability, publisher configuration or previous results. Each run builds
its own candidate; files are not promoted between separate workflow runs.
Upload concurrency is scoped to the index and project version. Different versions
and indices do not share an upload lock. Runs for the same index/version do not
upload concurrently; GitHub may replace an older pending job when another is
queued. This is not a durable release queue or a substitute for checking an
index before retrying.

Development (`.devN`), alpha, beta, rc and stable version tags all publish to
PyPI. TestPyPI validates changes to the publishing process; it is not a daily
development-release channel and is not required again after a successful rehearsal.

## Public contributions and release authority

The intended deployment is a **public repository accepting external pull
requests**. Repository privacy and the current number of collaborators are not
security controls for this design.

| Actor | Allowed activity | Boundary |
| --- | --- | --- |
| External contributor | Fork, open a PR, run approved PR checks | No upstream write access, publishing environment or publishing identity |
| Code maintainer with write access | Review/merge according to main rules; dispatch checks/rehearsals | Dispatch does not grant production approval or permission to create release tags |
| Release maintainer | Create authorized version tags and approve production uploads | Tag rules and the `pypi` environment must enforce this role outside workflow code |

PR checks run untrusted contributor code via `pull_request` on GitHub-hosted
runners with read-only repository permissions. CI has no publishing environment,
OIDC permission or inherited secrets. Do not change this to execute PR code under
`pull_request_target`, an elevated `workflow_run`, or a persistent self-hosted
runner. Release jobs consume only their own run's artifact ID; they do not accept
an artifact supplied by a PR or another workflow run.

A fork's workflow has the fork's identity, not the upstream publisher identity.
Nevertheless, review third-party changes to workflows, release scripts, build
configuration and dependencies carefully before merging. Someone who can change
the trusted workflow can remove its Python guards. Tag/version checks
catch mistakes; they do not replace GitHub rules and environment approval.
Trusted Publishing authenticates the workflow, not the safety of package code.

## One-time owner configuration

These settings live outside Git; adding the workflow does not configure them.
After the [public-content review](#before-making-this-repository-public), the owner
can change visibility under **Settings → General → Danger Zone** when authorized.
Configure the controls below before enabling uploads; public visibility alone
sets none of them. TestPyPI accounts, publishers and environments are optional.

1. Create an account on [PyPI](https://pypi.org/) for production and optionally
   a separate account on [TestPyPI](https://test.pypi.org/) for rehearsals. Verify
   email, enable 2FA, and keep recovery codes outside the repository. Confirm the
   project name is available on the selected index. Neither a repository nor a
   pending publisher reserves a package name.
2. In GitHub **Settings → Environments**, create `pypi` for production and
   optionally `testpypi` for rehearsals. For `pypi`, allow only **tags** matching
   `v*`, not branches. For `testpypi`, allow only the **branch** `main`, not tags.
   Require reviewer approval for both environments and disable administrator
   bypass. A sole maintainer can explicitly self-approve; enable prevention of
   self-review once another release maintainer can approve.
3. While the repository is still private, check your GitHub plan before
   configuring publishers. Free private repositories
   do not provide the environment protections this workflow expects. Pro/Team
   support private environments and deployment branch/tag restrictions, but
   Required reviewers on these plans are still limited to public repositories.
   Upgrading to Pro alone does not provide private production approval. Until
   approval is available, leave the production publisher unconfigured. Use
   `check` without publishing; even TestPyPI rehearsal requires an available
   environment. Do not remove environment bindings to work around plan limits.
   An environment name alone is not an approval gate.
4. Verify the publisher identity against the table below. Keep an existing matching
   publisher; for a new project configure a pending publisher, or use the existing
   project's Publishing settings. A pending publisher creates the project on its
   first successful upload. No API token is needed.
5. Under **Settings → Rules → Rulesets**, create active rules for `main`: require
   PRs, block force pushes and deletion, and require these five checks:
   `Validate GitHub Actions workflows`, `Check (Python 3.12)`,
   `Check (Python 3.13)`, `Check (Python 3.14)` and `Check (Python 3.15)`.
   Do not require the independent `Audit locked dependencies` notification job.
   Review all external PRs. With at least two trusted maintainers, require approving reviews,
   dismiss stale approvals and require code-owner review for `.github/workflows/`,
   `scripts/`, `pyproject.toml` and `uv.lock` through `CODEOWNERS`. A sole maintainer
   cannot independently approve their own PR; do not claim two-person review or
   enable requirements that prevent all maintainer updates without a considered
   exception policy.
6. Protect `v*` tags with active rulesets. Limit creation to release maintainers
   and prohibit updates/deletion. The sole owner can use the Repository admins
   role for creation bypass; do not hard-code an account name in CI. Keep the
   creation exception separate from the update/deletion rules so a creation bypass does not also allow moving a release
   tag. Review any bypass roles; repository write access should not automatically
   grant release authority. GitHub rulesets enforce tag protection; the workflow
   does not infer it from `github.ref_protected`, which has been reported false
   even for protected tags. Inspect the active rules and bypass list before
   enabling publication. The upload environment separately restricts deployment
   to version tags and requires approval.
   Public repositories support these rules on Free;
   private repository rulesets require Pro, Team or Enterprise.
7. Under **Settings → Actions → General**, use read-only default workflow
   permissions, leave workflow PR creation/approval disabled, and require approval
   for fork workflows from outside collaborators. Such approval permits running
   untrusted tests; it is not approval to merge or publish. Keep release jobs free
   of repository secrets and restrict write/admin access to trusted maintainers.
   Use 2FA for maintainer accounts and review authorized apps/tokens as well.
   The GitHub Release job explicitly requests `contents: write` with the built-in
   `GITHUB_TOKEN`; no personal access token or write-enabled default is needed.
   Organization policies must permit this job-level permission and the pinned
   actions used by the workflow.
8. Under **Settings → Advanced Security / Code security**, enable the Dependency
   graph, Dependabot alerts and security updates, plus available secret scanning
   and push protection. Enable private vulnerability reporting and confirm
   **Security → Advisories → Report a vulnerability** is available, matching
   `SECURITY.md`. Enable CodeQL's default Python setup and inspect its first
   result; no additional custom CodeQL workflow is needed. These controls do not
   make the deferred Python dependency updater compatible with the required uv;
   see [dependency maintenance](../CONTRIBUTING.md#environment-and-dependency-maintenance).
9. Subscribe through **Watch → Custom → Security alerts**. Under personal
   **Settings → Notifications → Actions**, enable Email or On GitHub delivery
   (optionally failed workflows only). Inspect the first weekly dependency audit
   and hosted dependency update. Scheduled-run notifications follow the workflow
   creator, last cron editor or person who re-enabled it; that account must remain
   an active maintainer. Keep the audit advisory rather than a required merge check.

Before the first public release, verify these settings in GitHub (not only the
YAML). Check that a fork PR runs only unprivileged CI, cannot deploy to `pypi`, and
cannot introduce an artifact into a release run. Confirm that unauthorized tag
creation is rejected and each upload waits for the designated approver.
Actionlint checks workflow syntax; review permissions, job dependencies and
artifact inputs in the workflow itself. Library unit tests do not assert workflow
text, and static checks do not prove that live rules or OIDC work. Confirm these
through the authorized rehearsal and production runs. Do not turn on production
publishing until the required platform controls are in place. Changing visibility
alone does not configure them.

| Publisher field | TestPyPI | PyPI |
| --- | --- | --- |
| Project name | `typebind` | `typebind` |
| Repository owner | `nicholas-1vbw` | `nicholas-1vbw` |
| Repository name | `typebind` | `typebind` |
| Workflow filename | `publish.yml` | `publish.yml` |
| Environment | `testpypi` | `pypi` |
| Pending publisher settings | [TestPyPI settings](https://test.pypi.org/manage/account/publishing/) | [PyPI settings](https://pypi.org/manage/account/publishing/) |

Do not create a long-lived token as a fallback. If OIDC fails, check the owner,
repository, filename and environment match exactly, then inspect the job error.
The action produces index-hosted attestations by default. Third-party actions
are pinned to commit SHAs; review their updates as dependency changes.

## Environment policy

CI and publishing share the runner and toolchain policy in
[Contributing](../CONTRIBUTING.md#environment-and-dependency-maintenance).
Workflow files own runner labels; `.python-version`, `pyproject.toml` and
`uv.lock` own their respective Python, tool and dependency selections.

## Prepare and check a candidate

1. Change `project.version` in `pyproject.toml`, run `uv lock`, and review the
   release notes and public package contents. Keep version changes in a PR.
   Use canonical versions such as `0.1.0.dev1` or `0.1.0a1`. The local plugin
   examples consume this checkout, not independently selected typebind releases.
   Their own fixture versions do not need to change with the library version.
2. Run `uv sync --locked` followed by `uv run --no-sync poe check` locally and require the full hosted CI matrix
   on the final commit. Merge the reviewed changes to `main`.
3. To check without uploading, dispatch the current `main` snapshot:

   ```sh
   gh workflow run publish.yml --ref main -f target=check
   ```

4. Choose the next action: use the manual TestPyPI procedure below for a rehearsal,
   or create a production tag only when ready to publish to PyPI. A rehearsal
   does not need a version tag.

Inspect the wheel and sdist under the `distributions` artifact before approving
an upload. The artifact contains package files only, not verification logs.
Artifacts expire after 14 days; they are transfer storage, not a permanent
release archive.

## Optional TestPyPI rehearsal

For the first release, or after changes to the build backend, publishing workflow
or authentication setup, use a reviewed main candidate with an unused version
such as `0.1.0.dev1`. After merging the version PR, dispatch:

```sh
gh workflow run publish.yml --ref main -f target=testpypi
```

Do not create a production tag for this rehearsal. The guard requires the main
branch, verifies that the checkout matches the workflow SHA and belongs to main,
and reads the version from `pyproject.toml`. A later main commit does not change
the candidate SHA of an already started run. Dispatching from a tag or another
branch fails before upload.

Shared checks and artifact validation run before the `testpypi` approval. Inspect
the candidate SHA, version and artifact, then approve. Only TestPyPI receives the
upload; the workflow verifies its installed wheel and skips production and
GitHub Release creation. This validates the test publisher, not the production
publisher's configuration. Once the rehearsal succeeds, subsequent development
and other releases can use PyPI directly.

## Production release

Create and push a version tag on the intended reviewed main commit, for
example `v0.1.0` for version `0.1.0`. The publish guard requires the exact
`v<project.version>` tag, checks that checkout/tag/workflow identify the same
commit, and verifies that commit belongs to `main`. An older main commit is
allowed; a branch or an unmerged commit is not. This push **starts production**,
so finish the one-time settings before pushing the tag. For example, after
selecting the reviewed commit locally:

```sh
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin refs/tags/v0.1.0
```

Push one release tag at a time using your authorized maintainer credentials.
GitHub does not trigger a new push workflow for tags pushed using a workflow's
`GITHUB_TOKEN`. Do not use a GitHub Release as a way to create the version tag;
this workflow creates the Release after verifying PyPI.

The tag-push production run performs these steps in order, without contacting
TestPyPI. Open **Actions → Publish** for that tag; no manual dispatch is needed:

1. Run the reusable full CI matrix at the tagged commit (not a previous run's result).
2. Build the sdist and wheel once for publication, then validate them using the
   existing `poe check-dist` entry point.
3. Wait for the configured `pypi` environment approval. Inspect the selected
   tag/SHA, successful checks and candidate artifact, then approve.
4. Upload the **same validated workflow artifact** to PyPI, without rebuilding.
5. Install the PyPI wheel into a fresh environment using `uv pip`, with cache
   disabled, no dependencies and a required SHA256 derived from the candidate
   wheel. Then let uv resolve the installed wheel's declared dependencies from
   ordinary PyPI, using the exported runtime lock as constraints, and run
   `uv pip check`. The lock does not preinstall undeclared dependencies; TestPyPI is never
   a dependency index. Local artifact checks install the wheel with dependency
   resolution enabled directly.
   Run the model, manual assembly and decorator examples outside the checkout,
   plus strict Pyright against the installed package.
6. Create a GitHub Release for the existing tag with generated release notes and
   the same wheel/sdist as assets. Native `gh release create --verify-tag` refuses
   to create a missing tag. The project's PEP 440 version determines whether the
   GitHub Release is marked as a prerelease, including development versions.

Both paths verify the wheel downloaded from their selected index against the
artifact built in that run. There is no cross-run or cross-index artifact reuse
or hash comparison. TestPyPI is not an automatic release gate.

Build and verification jobs have no OIDC permission. Upload jobs have only
`id-token: write`, download a specific artifact ID from the current run, and
never check out or execute repository code. The final GitHub Release job has
only `contents: write`, no OIDC, and runs the native GitHub CLI without checking
out repository code. It depends on successful PyPI verification; a failed,
cancelled or skipped verification prevents release creation. No PR event starts
this workflow.
The environment and tag protections must also be configured: YAML checks do not
replace GitHub access control.

For local read-only verification after an index upload, retain the expected
artifacts and run:

```sh
uv sync --locked
uv run --no-sync poe check-dist --dist-dir dist/release --index-url https://test.pypi.org/simple/
```

This verifies an installed **wheel**. The sdist is checked locally and used by
`uv build` to produce the wheel; this does not claim a second installation of the
sdist downloaded from the index. Plugin wheel installation/discovery is covered
by the full CI matrix, separately from these installed-package examples.

## Retries and failure boundaries

Repeated TestPyPI rehearsals may encounter the same version, so the test upload
tolerates existing filenames. The following install still requires the candidate wheel's
exact hash; an existing filename is not sufficient. A separate dispatch builds a
new candidate, not a reference to the earlier run. If its bytes differ, stop and
investigate; never remove the hash check or move an existing release tag. Prefer
a new version for changed contents.

PyPI uploads do not skip existing files. A network or post-upload verification
failure can occur **after publication has succeeded**, or after only some files
have been accepted. Inspect the index before retrying. Re-run failed jobs only
when the original artifact remains available and the missing step is safe to
repeat; do not rerun an already successful upload. If an upload accepted only some
files and cannot safely resume, prepare a new version and approve it separately;
do not enable production `skip-existing` as a workaround. Published filenames
cannot be overwritten, and deleting a release does not make them reusable.

There is no custom retry loop: if the index has not exposed an uploaded wheel yet,
the verification job fails and can be rerun after checking the index. Failure of
post-PyPI verification means **published, verification failed**, not rollback;
it also prevents GitHub Release creation. Rerun that failed verification (and its
downstream Release job) rather than uploading again.

If only GitHub Release creation fails, PyPI remains published and verified.
Inspect the Releases page and any partial draft/assets before retrying only the
failed job. The workflow does not overwrite an existing Release or clobber
assets. If a Release already exists, reconcile its tag and assets manually;
do not delete/recreate a version tag or repeat the PyPI upload. The native CLI
handles release creation; this workflow adds no custom retry or replacement API.

## Before making this repository public

A private repository does not make TestPyPI or PyPI packages private. Uploading
an sdist exposes the source, included tests/examples/docs, package metadata and
license. Trusted Publishing attestations also reveal repository/workflow/commit
identity. Decide that this content is ready to be public before either upload.

Before changing GitHub visibility, inspect the full Git history, branches/tags,
PRs/issues, Actions logs and retained artifacts for credentials, private paths,
internal URLs and material that must not become public. Inspect the actual
wheel/sdist as well as `.gitignore`; ignored files can still exist in old commits.
If credentials were exposed, revoke/rotate them before cleanup. This workflow is
not a historical secret audit and does not change repository visibility.

## References

- [Trusted Publishing security model](https://docs.pypi.org/trusted-publishers/security-model/)
- [GitHub Actions secure use](https://docs.github.com/en/actions/reference/security/secure-use)
- [PyPA publishing guide](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)
- [Creating a project with Trusted Publishing](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
- [GitHub environment protection availability](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [Reported tag `ref_protected` behavior](https://github.com/rcwbr/GitHub-ref_protected-false-for-tags)
- [GitHub ruleset availability](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
- [GitHub CLI release creation](https://cli.github.com/manual/gh_release_create)
- [GitHub workflow trigger behavior](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
- [Using TestPyPI](https://packaging.python.org/en/latest/guides/using-testpypi/)

The build/artifact/upload separation follows the same pattern used by
[packaging](https://github.com/pypa/packaging/blob/main/.github/workflows/publish.yml)
and [attrs](https://github.com/python-attrs/attrs/blob/main/.github/workflows/pypi-package.yml).
Typebind uses protected tag pushes to start production and environment approval
to authorize upload; manual dispatch remains available for checks and rehearsals.
