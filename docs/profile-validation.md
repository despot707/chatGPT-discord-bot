# Private profile validation

The isolated profile implementation was tested against the pinned dependencies on
Python 3.12 in GitHub Actions. The test-first commit intentionally failed for the
missing modules. Initial full-suite checks also exposed 21 pre-existing failures:
passive memory enabled in plain client fixtures and stale expectations for removed
jailbreak personas. The default is now off, production composition disconnects the
collector, and the persona tests assert the current supported behavior.

The repair run 36511904743 verified the committed working tree at
8d3f4aa21a0f8f2a7e693ded58f99778499c4525: lint, formatting, type checking,
the entire pytest suite and compilation all succeeded. Two pre-existing Linux
root-only permission tests are skipped on the unprivileged GitHub runner.

Tests cover private default projection, numeric account/server scoping, restart
persistence, invalid dates and input, conflicting forms, owner-only interactions,
private errors and replies, real Discord component serialization and modal layout,
strict model proposals, and scoped legacy-data deletion. Setup does not require
external OAuth or passwords. No general security certification or Discord policy
approval is claimed.

The production deployment and standard multi-platform CI status must be checked
for the final commit independently. There has not been a visual end-to-end click
through inside a logged-in Discord client from this execution environment.
