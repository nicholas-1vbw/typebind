# Security policy

## Report a vulnerability privately

Use [Report a vulnerability](https://github.com/nicholas-1vbw/typebind/security/advisories/new)
on the repository's Security tab. Include the affected version, a minimal
reproduction, expected and actual behavior, and the impact. Remove credentials
and private data from examples. Do not publish exploit details in an issue or PR
before coordinating disclosure with the maintainer.

This channel requires the owner to enable GitHub private vulnerability reporting
when the repository becomes public. If the button is unavailable, open an issue
asking for a private reporting channel without including vulnerability details.
No response-time or remediation SLA is promised.

## Supported releases

Before the first stable release, fixes target the latest published prerelease.
After a stable release, fixes target the latest release; upgrading may be
necessary. Older release branches do not have a separate backport commitment.
Report suspected problems even if you cannot yet reproduce them on that version.

## Trust boundaries

Typebind parses model data using Pydantic and assembles explicitly selected model
and behavior classes. Discriminator tags identify branches; they do not authorize
a caller. Applications own authentication, authorization, input size limits and
resource budgets.

Entry point loading imports and executes installed Python packages. Behavior
implementations are also trusted executable code. Family checks and source
scopes are not a sandbox, and post-import selection cannot undo import side
effects. Install only plugins you trust. Reports of bypassed selection, incorrect
branch validation or other unexpected behavior remain welcome; these boundaries
do not exempt defects in the library from investigation.

The persistence contract is Pydantic JSON round trips. Pickle is not a supported
interchange format. Dependency alerts and audits detect known dependency issues;
they do not replace reports about typebind itself or a security review.
