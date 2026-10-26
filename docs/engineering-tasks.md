# Engineering tasks

[README](../README.md) · [Repository configuration](repository-configuration.md)

SecondShift executes tasks defined by repository skills. A task can investigate
code, produce a briefing, or change connected service data through explicitly
approved MCP tools or HTTP API operations.

The current [examples](../examples/README.md) are reviews and briefings that
return findings without modifying code or connected services. Those are the
examples' chosen boundaries. Root `.agents/skills` contains discovery symlinks.

Choose a workflow from the [skill catalog](../examples/README.md). Each example
explains its data sources and suggests a schedule where appropriate. This guide
covers the shared setup process.

**Begin with a manual run, check its results and tool activity, then enable a
schedule if useful.** Verify that the task can access its intended data and
complete the intended work before it runs unattended. For a review, check the
findings and coverage. For an action task, verify the resulting service state.

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

## Tasks that change service data

Custom MCP and HTTP API connections can expose write operations. The workspace
owner must allow the operation on the connection and approve the job's request.
The built-in GitHub and Sentry connections expose read tools only. Use a custom
connection with the required operations for actions such as Jira transitions.

For example, a Jira issue cleanup skill could:

1. Find candidate issues in the approved project.
2. Check linked implementation, validation, and deployment evidence against
   the team's definition of fixed. A merged PR alone may not meet that definition.
3. Transition only issues that meet those criteria and verify the resulting state.
4. Summarize changed issues, skipped issues, and failures with evidence links.

Select the service's actual read and transition operations, then declare their
exact names in the job's `requires` section. Keep the project, credentials, and
endpoint configuration in the workspace connection. This workflow is a design
example. The shipped catalog does not include a Jira cleanup skill.

Write the skill's completion criteria and stopping conditions explicitly.
Check current state before acting, skip already-completed transitions, and
avoid duplicate comments on reruns. If a write times out, check its outcome
before retrying. Start with a read-only preview of proposed changes when useful,
then test the approved action workflow manually before enabling its schedule.
Access approval permits the tool calls. It does not guarantee that the model
will apply your business rules correctly. See the
[security model](security-model.md#remaining-risks).

## Execution environment

The sandbox contains the repository at the run's commit, without network access.
Git history and project dependencies may be absent. Historical comparisons need
available Git data or approved tools. Each skill reports unavailable evidence
instead of treating it as proof that nothing is wrong.
