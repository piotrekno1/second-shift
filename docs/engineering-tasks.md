# Engineering tasks

[README](../README.md) · [Repository configuration](repository-configuration.md)

These five review skills live in [self-contained examples](../examples/README.md),
with discovery symlinks in [`.agents/skills`](../.agents/skills). They can run as
SecondShift jobs or be used by a coding agent that discovers repository skills.
Each investigates a specific problem and returns a report. They do not change
service state or repository files.

| Skill | Required evidence |
| --- | --- |
| [CI failure review](../examples/ci-failure-review/.agents/skills/ci-failure-review/SKILL.md) | Build history and failure logs from an approved API or MCP connection. |
| [Production error review](../examples/production-error-review/.agents/skills/production-error-review/SKILL.md) | Sentry issues, errors, or traces from the built-in connection, or equivalent approved API tools. |
| [Monitoring coverage review](../examples/monitoring-coverage-review/.agents/skills/monitoring-coverage-review/SKILL.md) | Alert definitions in Git or an approved monitoring connection. History is needed to establish changes. |
| [Release readiness review](../examples/release-readiness-review/.agents/skills/release-readiness-review/SKILL.md) | A release target, baseline, and rollout instructions in Git or an approved connection. |
| [Incident follow-up review](../examples/incident-follow-up-review/.agents/skills/incident-follow-up-review/SKILL.md) | Incident action items and acceptance criteria in Git or an approved connection, with links to implementation evidence. |

## Use them in SecondShift itself

The root `.secondshift.yaml` includes release readiness as a manual job. Commit
the skill and manifest, sync the repository, connect a model, and choose **Run
now**. No external service connection is required for repository-only review.
The skill looks for a committed release plan that identifies the target and
baseline. If those are missing, it reports that limit and checks current rollout
documentation for concrete inconsistencies.

The other four skills are available for local agent use but are not declared as
jobs in the root manifest. Configure the relevant data sources before adding
them. Monitoring and incident review can work from committed documents when
those contain the required evidence.

For an interactive agent, ask for a skill and scope explicitly, for example:

```text
Use $release-readiness-review to review the planned release described in our
committed release notes. Report any missing rollout steps and unavailable evidence.
```

Local agent use follows that agent's permissions. SecondShift's sandbox and
broker controls apply when the skill runs as a SecondShift job.

## Add a job to your repository

Copy the desired skill directory from `examples/<name>/.agents/skills` into
your own `.agents/skills` (copy the actual directory, not the root discovery
symlink), then add a job
to `.secondshift.yaml`. Start with a manual run before choosing a schedule:

```yaml
version: 1
jobs:
  release-readiness-review:
    name: Release readiness review
    skill: .agents/skills/release-readiness-review/SKILL.md
```

For live data, connect the service in SecondShift, choose its tools, and declare
those exact tool names under the job's `requires.mcps` or `requires.apis`.
Approve the job's request after syncing the committed configuration. A skill
file does not grant access by itself.

For example, a production error review can use the built-in Sentry connection:

```yaml
version: 1
jobs:
  production-error-review:
    name: Production error review
    skill: .agents/skills/production-error-review/SKILL.md
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
connection. Begin with a manual run, check its findings and coverage, then add
a schedule if useful.

CI, monitoring, and incident tools depend on the service you connect. Select
operations that expose the evidence in the table above. Use the tool names shown
by SecondShift rather than guessing names. The built-in GitHub connection reads
pull requests and commits. It does not provide build logs. See
[MCP and API declarations](repository-configuration.md#mcp-and-api-declarations)
for connection setup and [webhook notifications](repository-configuration.md#deliver-reports-to-a-webhook)
for report delivery.

The sandbox contains the repository at the run's commit, without network access.
Git history and project dependencies may be absent. Historical comparisons need
available Git data or approved tools. Each skill reports unavailable evidence
instead of treating it as proof that nothing is wrong.
