# Repository health

Use the [skill](.agents/skills/repository-health/SKILL.md) to investigate a repository
and report actionable findings without changing files or service state.

## Evidence and setup

Source, configuration, contributor guidance, and tests from the repository snapshot.

Only a model provider is required. Focused offline checks can run when their dependencies are already available. The skill does not install packages or assume a particular language or framework.

## Suggested schedule

Mondays at 07:00 UTC. Reviews the synchronized repository snapshot. Remove `schedule` for manual use only.

**Begin with a manual run, check its findings and coverage, then enable a
schedule if useful.**

## Use in another project

1. Copy this example's `.agents/skills/repository-health` into your repository's
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
scope and available evidence. Use that agent's approved tools. Installing a
skill does not grant access. The skill returns a report and leaves delivery to
its host. See [repository configuration](../../docs/repository-configuration.md)
for connections, schedules, and optional webhook delivery.

## Use in this repository

The canonical skill lives in this example. Root `.agents/skills/repository-health`
is a relative symlink for agent discovery. Root jobs reference canonical example
paths directly. Copying a sample manifest does not enable a job or grant access.

This repository already declares this job for manual use in its root manifest.
