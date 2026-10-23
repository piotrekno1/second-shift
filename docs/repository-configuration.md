# Repository configuration

[README](../README.md) · [Architecture](architecture.md) · [Self-hosting](self-hosting.md)

A root-level `.secondshift.yaml` declares what runs and when. The server
controls models, credentials, sandbox settings, and runtime policy.

Commit the manifest and its referenced skill files before adding or syncing
the repository in SecondShift. Runs use the synchronized Git revision.

## Your first job

Create `.agents/skills/repository-review/SKILL.md`:

```markdown
# Repository review

Read the README and inspect the source for correctness problems and outdated
documentation. Run the documented tests if their dependencies are available.
Do not install packages, use the network, or modify files.

Report confirmed findings with file paths, line numbers, user impact, and a
suggested fix. List the checks you ran and anything you could not verify.
```

Add `.secondshift.yaml`:

```yaml
version: 1

jobs:
  repository-review:
    name: Weekly repository review
    skill: .agents/skills/repository-review/SKILL.md
    schedule: "0 9 * * 1"
    timezone: Europe/Warsaw
```

Commit both files, add the repository in SecondShift, and connect a default
model provider. Review and enable the discovered job, then choose **Run now**
to try it without waiting for the schedule.

The sandbox has no network access or provider credentials. Dependencies must
already be available in the sandbox image. Reading files and running
standard-library tests work without installing packages.

## Manifest fields

| Field | Required | Meaning |
| --- | --- | --- |
| `version` | No | Manifest version. Only `1` is supported and is the default. |
| `jobs` | Yes | Mapping of job keys to job definitions. |
| `jobs.<key>.name` | Yes | Display name in SecondShift. |
| `jobs.<key>.skill` | Yes | Repository-relative path to a `SKILL.md` file. |
| `jobs.<key>.schedule` | Yes | Cron schedule, such as `0 9 * * 1` for Monday at 09:00. |
| `jobs.<key>.timezone` | No | IANA timezone, such as `Europe/Warsaw`. Defaults to `UTC`. |
| `jobs.<key>.notify` | No | Webhook notification bindings requested by this job. |
| `jobs.<key>.requires` | No | MCP and API capability declarations. Execution is not supported yet. |

Skill paths must stay inside the repository. Unknown fields are rejected.
Use separate job definitions for skills with different schedules or access
requirements.

## Deliver reports to a webhook

Add a logical notification binding to the job:

```yaml
version: 1

jobs:
  repository-review:
    name: Weekly repository review
    skill: .agents/skills/repository-review/SKILL.md
    schedule: "0 9 * * 1"
    timezone: Europe/Warsaw
    notify:
      - binding: engineering-reports
        required: true
```

1. Create a webhook connection in SecondShift with the binding
   `engineering-reports` and your destination URL.
2. Send a test message to verify delivery.
3. Sync the committed manifest and approve the job's notification access.
4. Enable the job and start a run.

The server posts the run status, report summary, and a link back to SecondShift
after the run finishes. Delivery happens outside the offline sandbox.

Each notification needs a `binding`. Its `required` flag defaults to `true`.
Set it to `false` if a missing notification approval should not block the job.
Binding names use lowercase letters, numbers, and single dashes. A job cannot
reuse a binding across its notification, MCP, or API declarations.

Repository configuration must never contain webhook URLs, secrets, credential
references, provider endpoints, or OAuth scopes. The workspace owns those
settings and can revoke access independently.

## MCP and API declarations

The manifest can describe requested external tools, but the worker currently
rejects their execution. These declarations are for planned integration work,
not a working quickstart.

```yaml
requires:
  mcps:
    - binding: observability
      tools:
        - search_dashboards
        - query_metrics
      required: true
  apis:
    - binding: issue-tracker
      tools:
        - list_issues
      required: true
```

Place `requires` inside a job definition. Each entry needs a logical `binding`
and a nonempty list of unique `tools`. The `required` flag defaults to `true`.
Approvals do not make these tools executable in the current worker.

Follow [brokered MCP and API execution](https://github.com/piotrekno1/second-shift/issues/2)
for implementation progress.
