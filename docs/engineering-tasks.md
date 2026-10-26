# Engineering tasks

[README](../README.md) · [Repository configuration](repository-configuration.md)

The review skills in [examples](../examples/README.md) investigate repository
code and approved service data, then return actionable reports. They do not
change service state or repository files. Root `.agents/skills` contains
symlinks so coding agents can discover the same skills.

Choose a workflow from the [skill catalog](../examples/README.md). Each example
explains its data sources and suggests a schedule where appropriate. This guide
covers the shared setup process.

**Begin with a manual run, check its findings and coverage, then enable a
schedule if useful.** A successful run should show that the skill can access
the intended evidence and produce a useful report before it runs unattended.

For local coding-agent use, request a skill and scope explicitly, for example:

```text
Use $release-readiness-review to review the planned release described in our
committed release notes. Report missing rollout steps and unavailable evidence.
```

Local agent use follows that agent's permissions. SecondShift's sandbox and
broker controls apply when the skill runs as a SecondShift job.

## Add a job to your repository

Copy the desired skill directory from `examples/<name>/.agents/skills` into
your own `.agents/skills` (copy the actual directory, not the root discovery
symlink), then add a job to `.secondshift.yaml`.

For live data, connect the service in SecondShift, choose its tools, and declare
those exact tool names under the job's `requires.mcps` or `requires.apis`.
Approve the job's request after syncing the committed configuration. A skill
file does not grant access by itself.

A production error review can use the built-in Sentry connection:

```yaml
version: 1
jobs:
  production-error-review:
    name: Production error review
    skill: .agents/skills/production-error-review/SKILL.md
    schedule: "0 8 * * *"
    timezone: UTC
    requires:
      mcps:
        - binding: sentry
          tools:
            - search_issues
            - search_errors
            - search_traces
            - get_sentry_resource
```

Create the Sentry connection with binding `sentry` and choose the organization
or project to review. Confirm that the requested tools are available on that
connection. Use **Run now** to verify the report before enabling the daily schedule.

CI, monitoring, and incident tools depend on the service you connect. Select
operations that expose the history and records described in each example. Use
the tool names shown by SecondShift rather than guessing names. The built-in GitHub connection reads
pull requests and commits. It does not provide build logs. See
[MCP and API declarations](repository-configuration.md#mcp-and-api-declarations)
for connection setup and [webhook notifications](repository-configuration.md#deliver-reports-to-a-webhook)
for report delivery.

The sandbox contains the repository at the run's commit, without network access.
Git history and project dependencies may be absent. Historical comparisons need
available Git data or approved tools. Each skill reports unavailable evidence
instead of treating it as proof that nothing is wrong.
