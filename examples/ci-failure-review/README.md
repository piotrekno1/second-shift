# Recurring CI failures

Use the [skill](.agents/skills/ci-failure-review/SKILL.md) to investigate a repository
and report actionable findings without changing files or service state.

## Evidence and setup

Build history, job logs, attempt numbers, branches, and commit SHAs from an approved CI API or MCP service. The built-in GitHub connection does not expose build logs.

Connect your CI service, select its read operations for build history and job logs, and declare their exact names under `requires.mcps` or `requires.apis`. This provider-neutral template deliberately omits guessed tool names. Without that access, it reports missing evidence rather than diagnosing failures from code.

## Suggested schedule

Weekdays at 08:00 UTC; reviews the preceding seven days. Remove `schedule` for manual use only.

**Begin with a manual run, check its findings and coverage, then enable a
schedule if useful.**

## Use in another project

1. Copy this example's `.agents/skills/ci-failure-review` into your repository's
   `.agents/skills`, and merge this example's `.secondshift.yaml` into your root
   manifest. Copy the actual example directory, not the discovery symlink from
   this repository's root `.agents` directory. Commit the files.
2. Connect a model provider and choose a default model. Configure and declare
   any service access described above. Connection endpoints and credentials
   belong in SecondShift, never in the manifest or skill.
3. Import or resync the repository, approve the requested access, and choose
   **Run now**. Review findings and coverage before enabling the proposed
   schedule. Adjust `schedule` and `timezone` for your team.

For local coding-agent use, copy the same skill and request it explicitly with
scope and available evidence. Use that agent's approved tools; installing a
skill does not grant access. The skill returns a report and leaves delivery to
its host. See [repository configuration](../../docs/repository-configuration.md)
for connections, schedules, and optional webhook delivery.

## Use in this repository

The canonical skill lives in this example; root `.agents/skills/ci-failure-review`
is a relative symlink for agent discovery. Root jobs reference canonical example
paths directly. Copying a sample manifest does not enable a job or grant access.

This sample is not registered in the root manifest.
