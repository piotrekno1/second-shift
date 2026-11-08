---
name: code-simplifier
description: Review recently changed code for simplifications that keep behavior the same, such as duplicate lookups, loose types, dense one-liners, and stale comments.
---

# Code simplifier

Read [Sentry's Code Simplifier](references/sentry-code-simplifier.md) next to
this skill and use it as the review method. The bundled reference comes from
[Sentry's upstream skill at a pinned revision](https://github.com/getsentry/skills/blob/e94f22214528d69899d09c627b3315ddc3544816/skills/code-simplifier/SKILL.md)
and is licensed under [Apache 2.0](references/LICENSE).

The reference lists TypeScript and React standards as an example. Use the
repository's own contributor guidance, such as `AGENTS.md` or `CLAUDE.md`,
in their place.

Review the code changed by recent commits. Use the scope in the request when
it gives one. Otherwise use the commits from the last seven days. Skip release
commits, documentation-only changes, generated files, and lockfiles. Review
the repository without changing source files or service state.

Keep only suggestions that leave behavior unchanged and make the code easier
to read. Leave out style preferences the project's formatter or guidance
already settles. When a suggestion depends on a type, a caller, or a test,
check it in the code before you report it.

For each suggestion, give the location, what to change, and why it reads
better. Group them by how much they help, and name any you checked and
rejected because they would change behavior. Return the review as your final
report.
