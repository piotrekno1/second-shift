# Release readiness

Use the [skill](.agents/skills/release-readiness-review/SKILL.md) to investigate a repository
and report actionable findings without changing files or service state.

## Evidence and setup

An explicit release target, baseline, rollout instructions, and the relevant repository changes.

Provide a committed release plan identifying target and baseline, or supply them in the interactive request. External release plans need a separately declared, approved read connection. Without release scope, the skill reports the limitation and checks current documentation for concrete inconsistencies.

## Use in another project

1. Copy this example's `.agents/skills/release-readiness-review` into your repository's
   `.agents/skills`, and merge this example's `.secondshift.yaml` into your root
   manifest. Copy the actual example directory, not the discovery symlink from
   this repository's root `.agents` directory. Commit the files.
2. Connect a model provider and choose a default model. Configure and declare
   any service access described above. Connection endpoints and credentials
   belong in SecondShift, never in the manifest or skill.
3. Import or resync the repository, approve the requested access, and choose
   **Run now**. Review findings and coverage before adding a cron `schedule`
   and IANA `timezone`. This sample is manual by default.

For local coding-agent use, copy the same skill and request it explicitly with
scope and available evidence. Use that agent's approved tools; installing a
skill does not grant access. The skill returns a report and leaves delivery to
its host. See [repository configuration](../../docs/repository-configuration.md)
for connections, schedules, and optional webhook delivery.

## Use in this repository

The canonical skill lives in this example; root `.agents/skills/release-readiness-review`
is a relative symlink for agent discovery. Root jobs reference canonical example
paths directly. Copying a sample manifest does not enable a job or grant access.

This repository already declares this job for manual use in its root manifest.
