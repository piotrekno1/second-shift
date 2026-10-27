# Repository configuration

[README](../README.md) · [Architecture](architecture.md) · [Security model](security-model.md) · [Self-hosting](self-hosting.md)

A root-level `.secondshift.yaml` declares what runs and when. The server
controls models, credentials, sandbox settings, and runtime policy.

A skill defines a task: it can analyze repository files, gather service data,
or take actions through approved MCP tools and HTTP API operations. The review
below is a simple first example. See [action tasks](engineering-tasks.md#tasks-that-change-service-data)
for a workflow that changes connected service data.

Commit the manifest and its referenced skill files before adding or syncing
the repository in SecondShift. Runs use the synchronized Git revision.

A local repository follows the branch that was checked out when it was added.
SecondShift reads the manifest and skills from that branch's newest commit, so
edits in the folder and other branches never change what runs. The worker
checks the branch every five minutes and syncs new commits on its own. A commit
with a broken manifest is skipped, and runs keep the last good sync. Owners and
admins can turn auto-sync off on the repository's page and press **Resync**
instead. Syncing never grants access: a job that requests new access still
waits for an owner's approval.

## Your first job

**Begin with a manual run, check its results and tool activity, then enable a
schedule if useful.** A proposed schedule is a starting point. Verify the
skill's access and outcome before letting it run unattended.

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
model provider. Open the discovered job and choose **Run now** to try it. Enable
the job when you want it to run on schedule.

For a ready-made skill that needs only a model, see the
[documentation drift example](../examples/docs-drift/README.md).

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
| `jobs.<key>.schedule` | No | Cron schedule, such as `0 9 * * 1` for Monday at 09:00. Leave it out for a job that runs only with **Run now**. |
| `jobs.<key>.timezone` | No | IANA timezone, such as `Europe/Warsaw`. Defaults to `UTC`. |
| `jobs.<key>.history` | No | How many of the job's own earlier reports each run starts with, from 0 to 5. Defaults to 0. See [run history](#run-history). |
| `jobs.<key>.notify` | No | Webhook notification bindings requested by this job. |
| `jobs.<key>.requires` | No | MCP tools and HTTP API operations the job needs. See below. |

Skill paths must stay inside the repository. Unknown fields are rejected.
Use separate job definitions for skills with different schedules or access
requirements.

## Run history

A job that compares against its last run, such as a weekly review, can set
`history`. Each run then starts with the job's last N successful reports, newest
first, after the task in its prompt:

```yaml
jobs:
  weekly-review:
    name: Weekly review
    skill: .agents/skills/weekly-review/SKILL.md
    history: 1
```

Only the same job's reports are included: a job is its repository and key, so
renaming the key starts with no history. Failed and canceled runs are left out.
The reports are chosen when a run is created and are shown in its stored input.
Together they are capped by the server's `SECONDSHIFT_RUN_HISTORY_MAX_CHARS`,
newest first, so an older report may be cut or left out. One removed by
retention is marked as no longer stored. Reports are marked as earlier output, not instructions,
because they may quote untrusted text. Reading its own reports gives a job no
new access, so `history` needs no approval.

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
4. Start a run with **Run now**. Enable the job when you want it to run on schedule.

The server posts the run status, report summary, and a link to the run in
SecondShift after the run finishes. Failed runs are sent too, with the reason
they failed. Delivery happens outside the offline sandbox.

Each notification needs a `binding`. Its `required` flag defaults to `true`.
Set it to `false` if a missing notification approval should not block the job.
Binding names use lowercase letters, numbers, and single dashes. A job cannot
reuse a binding across its notification, MCP, or API declarations.

Repository configuration must never contain webhook URLs, secrets, credential
references, provider endpoints, or OAuth scopes. The workspace owns those
settings and can revoke access independently.

## MCP and API declarations

A job can ask for tools from an MCP server or operations from an HTTP API. The
repository names only a logical binding and the tools it needs. The workspace
owner connects the service, chooses which tools any job may use, and approves
each job's request.

```yaml
requires:
  mcps:
    - binding: github
      tools:
        - search_pull_requests
        - get_commit
      required: true
  apis:
    - binding: billing
      tools:
        - listCharges
      required: true
```

Place `requires` inside a job definition. Each entry needs a logical `binding`
and a nonempty list of unique `tools`. The `required` flag defaults to `true`.

### What a binding can connect to

| Connection | Tool names | Sign-in | Scope |
| --- | --- | --- | --- |
| GitHub (built in) | `search_pull_requests`, `pull_request_read`, `list_commits`, `get_commit` | Fine-grained personal access token | The job's own repository |
| Sentry (built in) | `find_projects`, `search_issues`, `search_errors`, `search_traces`, `get_sentry_resource` | Auth token with read scopes | The organization or project chosen when connecting |
| Custom MCP server | The server's own tool names | OAuth sign-in, or a bearer token | Whatever the credential can reach |
| HTTP API | The `operationId` of each operation in its OpenAPI 3 spec | Bearer token, or a named header | Whatever the credential can reach |

GitHub and Sentry offer only read tools that SecondShift allowlists. For a
custom MCP server, the owner chooses tools after connecting, and tools the
server marks read-only start selected. For an HTTP API, GET operations start
selected. The access review marks any requested tool that may change data.

An operation without an `operationId` gets a name built from its method and
path, such as `get_charges_chargeId`. The connection's edit form lists every
name.

### How calls run

Tool calls never run in the sandbox. The broker makes each call after checking
that the job's grant, the connection, and the tool definitions still match
the run's plan. A change stops the run.

For HTTP APIs, SecondShift builds every request from the spec. The model
supplies only the path and query values the spec declares, plus a JSON body for
operations that take one. Requests go only to the base URL the owner confirmed
when connecting. Redirects are not followed.

An approved tool can still be misused by a prompt injection. See
[remaining risks](security-model.md#remaining-risks) before approving tools
that change data.

See the [engineering briefing example](../examples/engineering-briefing/README.md)
for a working job that reads live GitHub data.

Need a service that does not fit these options?
[File an issue](https://github.com/piotrekno1/second-shift/issues/new) describing
the service and workflow you want to automate.
