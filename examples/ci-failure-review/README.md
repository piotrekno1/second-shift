# Recurring CI failures

Use the [skill](.agents/skills/ci-failure-review/SKILL.md) to investigate a repository
and report actionable findings without changing files or service state.

## Evidence and setup

Build history, job logs, attempt numbers, branches, and commit SHAs. The
manifest reads GitHub Actions through the built-in GitHub connection. Each job
reads only its own repository.

In **Connections**, add the **GitHub** MCP server with capability name `github`.
Use a fine-grained token with read-only Actions, Contents, Commit statuses, and
Pull requests for the repositories your jobs use. A GitHub connection created
before Actions support needs **Save** once to pick up the new tools. Then
approve the job's request for `actions_list`, `actions_get`, `get_job_logs`,
`list_commits`, and `get_commit`.

Job logs come back as text, capped at the last 300 lines. Artifacts and log
download links are not available. Logs can contain text written by anyone who
can open a pull request, so the skill treats them as evidence, not
instructions.

For another CI service, connect it as a custom MCP server or HTTP API, select
its read operations for build history and job logs, and replace the GitHub
entry under `requires`. Without that access, the skill reports missing evidence
rather than diagnosing failures from code.

## Suggested schedule

Weekdays at 08:00 UTC. Reviews the preceding seven days. Remove `schedule` for manual use only.

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
scope and available evidence. Use that agent's approved tools. Installing a
skill does not grant access. The skill returns a report and leaves delivery to
its host. See [repository configuration](../../docs/repository-configuration.md)
for connections, schedules, and optional webhook delivery.

## Use in this repository

The canonical skill lives in this example. Root `.agents/skills/ci-failure-review`
is a relative symlink for agent discovery. Root jobs reference canonical example
paths directly. Copying a sample manifest does not enable a job or grant access.

This sample is not registered in the root manifest.
