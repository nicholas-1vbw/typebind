# Releasing

Releases use standard wheel/sdist artifacts and the official
[`pypa/gh-action-pypi-publish`](https://github.com/pypa/gh-action-pypi-publish)
action with Trusted Publishing. No PyPI API token, `.pypirc`, or GitHub secret is
needed. GitHub issues a short-lived OIDC identity to each upload job; the index
checks that it matches the configured repository, workflow and environment.

The [Publish workflow](https://github.com/nicholas-1vbw/typebind/blob/main/.github/workflows/publish.yml) is **manual only**.
Merging a PR, pushing a branch or tag, or creating a GitHub Release does not upload
anything. Its default target, `check`, runs the full CI matrix and builds/verifies
a candidate without entering a publishing environment. Ordinary CI never uploads.
GitHub requires repository write access to dispatch the workflow. That ability
is separate from the configured production environment's approval. This workflow
does not create a GitHub Release or grant any job `contents: write`.

| Target | Path after shared CI, build and artifact validation |
| --- | --- |
| `check` (default) | Stop; no upload or publishing environment |
| `testpypi` | TestPyPI upload and installed-wheel verification |
| `pypi` | Production approval, PyPI upload and installed-wheel verification |

TestPyPI is an independent, optional rehearsal. Production does not depend on
its availability, publisher configuration or previous results. Each dispatch
builds its own candidate; files are not promoted between separate workflow runs.

## Public contributions and release authority

The intended deployment is a **public repository accepting external pull
requests**. Repository privacy and the current number of collaborators are not
security controls for this design.

| Actor | Allowed activity | Boundary |
| --- | --- | --- |
| External contributor | Fork, open a PR, run approved PR checks | No upstream write access, publishing environment or publishing identity |
| Code maintainer with write access | Review/merge according to main rules; dispatch workflows | Dispatch does not grant production approval or permission to create release tags |
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
the trusted workflow can remove its Python guards. Tag/version checks and tests
catch mistakes; they do not replace GitHub rules and environment approval.
Trusted Publishing authenticates the workflow, not the safety of package code.

## One-time owner configuration

These settings live outside Git; adding the workflow does not configure them.
Configure each index only when you intend to use it. TestPyPI accounts,
publishers and environments are not prerequisites for production publishing.

1. Create an account on [PyPI](https://pypi.org/) for production and optionally
   a separate account on [TestPyPI](https://test.pypi.org/) for rehearsals. Verify
   email, enable 2FA, and keep recovery codes outside the repository. Confirm the
   project name is available on the selected index. Neither a repository nor a
   pending publisher reserves a package name.
2. In GitHub **Settings → Environments**, create `pypi` for production and
   optionally `testpypi` for rehearsals. Allow
   deployments only from **tags** matching `v*`, not branches. Require approval
   for `pypi`; if another maintainer can approve, enable prevention of self-review.
   Restrict bypass permissions as appropriate for the maintainer team.
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
4. Configure a pending publisher on the selected index using the table below. For an
   existing project, add the publisher in that project's Publishing settings
   instead. The first successful upload creates a pending project.
5. Under **Settings → Rules → Rulesets**, create active rules for `main`: require
   PRs and the full CI checks, and block force pushes and deletion. Review all
   external PRs. With at least two trusted maintainers, require approving reviews,
   dismiss stale approvals and require code-owner review for `.github/workflows/`,
   `scripts/`, `pyproject.toml` and `uv.lock` through `CODEOWNERS`. A sole maintainer
   cannot independently approve their own PR; do not claim two-person review or
   enable requirements that prevent all maintainer updates without a considered
   exception policy.
6. Protect `v*` tags with active rulesets. Limit creation to release maintainers
   and prohibit updates/deletion. Keep the creation exception separate from the
   update/deletion rules so a creation bypass does not also allow moving a release
   tag. Review any bypass roles; repository write access should not automatically
   grant release authority. Public repositories support these rules on Free;
   private repository rulesets require Pro, Team or Enterprise.
7. Under **Settings → Actions → General**, use read-only default workflow
   permissions, leave workflow PR creation/approval disabled, and require approval
   for fork workflows from outside collaborators. Such approval permits running
   untrusted tests; it is not approval to merge or publish. Keep release jobs free
   of repository secrets and restrict write/admin access to trusted maintainers.
   Use 2FA for maintainer accounts and review authorized apps/tokens as well.

For `pypi`, select release maintainers as Required reviewers and disable routine
administrator bypass. A single maintainer can use explicit self-approval as a
release confirmation, but it is not separation of duties. Once another trusted
release approver is available, enable Prevent self-review. Inspect the exact
candidate SHA and artifact before approval, including workflow changes.

Before the first public release, verify these settings in GitHub (not only the
YAML). Check that a fork PR runs only unprivileged CI, cannot deploy to `pypi`, and
cannot introduce an artifact into a release run. Confirm that unauthorized tag
creation is rejected and production waits for the designated approver. The local
workflow tests check configuration contracts; they do not prove the live rules
are enabled. Do not turn on production publishing until these controls are in
place. Changing visibility alone does not configure them.

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

## Prepare and check a candidate

1. Change `project.version` in `pyproject.toml`, run `uv lock`, and review the
   release notes and public package contents. Keep version changes in a PR.
2. Run `uv run --locked poe check` locally and require the full hosted CI matrix
   on the final commit. Merge the reviewed changes to `main`.
3. To rehearse without uploading, run the following after this workflow has
   been merged. The command uses the current `main` snapshot:

   ```sh
   gh workflow run publish.yml --ref main -f target=check
   ```

4. Create and push a version tag on the intended reviewed main commit, for
   example `v0.1.0` for version `0.1.0`. The publish guard requires the exact
   `v<project.version>` tag, checks that checkout/tag/workflow identify the same
   commit, and verifies that commit belongs to `main`. An older main commit is
   allowed; a branch or an unmerged commit is not.

Inspect the wheel and sdist under the `distributions` artifact before approving
an upload. The artifact contains package files only, not verification logs.
Artifacts expire after 14 days; they are transfer storage, not a permanent
release archive.

## TestPyPI rehearsal and production release

For the first release, or after changes to the build backend, publishing workflow
or authentication setup, a TestPyPI rehearsal is recommended:

```sh
gh workflow run publish.yml --ref v0.1.0 -f target=testpypi
```

This runs the shared checks, uploads only to TestPyPI and verifies installation
from TestPyPI. It never enters the production environment. A successful rehearsal
validates the test publisher, not the production publisher's configuration.

For a production release:

```sh
gh workflow run publish.yml --ref v0.1.0 -f target=pypi
```

The production run performs these steps in order, without contacting TestPyPI:

1. Reuse the existing full CI matrix at the workflow's commit.
2. Build the sdist and wheel once for publication, then validate them using the
   existing `poe check-dist` entry point.
3. Wait for the configured `pypi` environment approval. Inspect the selected
   tag/SHA, successful checks and candidate artifact, then approve.
4. Upload the **same validated workflow artifact** to PyPI, without rebuilding.
5. Install the PyPI wheel into a fresh environment using `uv pip`, with cache
   disabled, no dependencies and a required SHA256 derived from the candidate
   wheel. Install locked runtime dependencies separately from ordinary PyPI.
   Run the model, manual assembly and decorator examples outside the checkout,
   plus strict Pyright against the installed package.

Both paths verify the wheel downloaded from their selected index against the
artifact built in that run. There is no cross-run or cross-index artifact reuse
or hash comparison. Rehearse first when appropriate, then start production as a
separate owner action; TestPyPI is not an automatic release gate.

Build and verification jobs have no OIDC permission. Upload jobs have only
`id-token: write`, download a specific artifact ID from the current run, and
never check out or execute repository code. No PR event starts this workflow.
The environment and tag protections must also be configured: YAML checks do not
replace GitHub access control.

For local read-only verification after an index upload, retain the expected
artifacts and run:

```sh
uv run --locked poe check-dist --dist-dir dist/release --index-url https://test.pypi.org/simple/
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
repeat; do not rerun an already successful upload. Published filenames cannot be
overwritten, and deleting a release does not make those filenames reusable.

There is no custom retry loop: if the index has not exposed an uploaded wheel yet,
the verification job fails and can be rerun after checking the index. Failure of
post-PyPI verification means **published, verification failed**, not rollback.

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
- [GitHub ruleset availability](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)
- [Using TestPyPI](https://packaging.python.org/en/latest/guides/using-testpypi/)

The build/artifact/upload separation follows the same pattern used by
[packaging](https://github.com/pypa/packaging/blob/main/.github/workflows/publish.yml)
and [attrs](https://github.com/python-attrs/attrs/blob/main/.github/workflows/pypi-package.yml).
Typebind uses manual dispatch to keep publication an explicit owner action.
