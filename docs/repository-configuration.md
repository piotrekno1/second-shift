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
instead. Syncing grants access only through connections that do not require
approval, and only for tools the owner allowed. Other requests wait for an
owner.

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
| `jobs.<key>.schedule` | No | Cron schedule, such as `0 9 * * 1` for Monday at 09:00. Leave it out for a job that runs only with **Run now**. A tick is skipped while the job has a run that has not finished. |
| `jobs.<key>.timezone` | No | IANA timezone, such as `Europe/Warsaw`. Defaults to `UTC`. |
| `jobs.<key>.history` | No | How many of the job's own earlier reports each run starts with, up to the server's `SECONDSHIFT_JOB_HISTORY_MAX` (5 by default). Defaults to 0. See [run history](#run-history). |
| `jobs.<key>.notify` | No | Webhook notification bindings requested by this job. |
| `jobs.<key>.triggers` | No | Events that can start the job. Only `webhook` is supported. An owner or admin turns it on. See [webhook triggers](#webhook-triggers). |
| `jobs.<key>.webhook` | No | Which webhook requests start a run: `skip_when`, `repeat_key` and `repeat_window`. Needs `triggers: [webhook]`. See [ignore requests and repeats](#ignore-requests-and-repeats). |
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

Reports are kept as long as their runs, `SECONDSHIFT_RUN_RETENTION_DAYS` (90 by
default). A job that runs less often than that, such as a quarterly review,
finds its previous report already removed: raise the retention or leave
`history` off for that job.

## Webhook triggers

A webhook trigger starts the job when a request arrives at its URL, for
example from an alerting tool. Name it in the job:

```yaml
jobs:
  alert-triage:
    name: Alert triage
    skill: .agents/skills/alert-triage/SKILL.md
    triggers: [webhook]
```

An owner or admin turns the trigger on from the job page. This creates its URL
and secret. Copy the secret then. It is not shown again. Neither the URL nor the
secret goes in the repository.

Each request must carry the secret in one of two ways:

- `Authorization: Bearer <secret>`. Any scheme works.
- A hex HMAC-SHA256 of the body, keyed with the secret, in
  `X-SecondShift-Signature`. A `sha256=` prefix is allowed. To block replays,
  also send a Unix timestamp in `X-SecondShift-Timestamp` and sign
  `<timestamp>:<body>`. Requests older than five minutes are then refused.

Tools that sign with their own header names usually let you rename them. Set
them to the names above.

### The request body

A run started by a webhook keeps the request body. The skill reads it with the
`trigger_payload` tool. The body is untrusted data, not instructions. Anyone who
can edit what the sender sends, such as an alert rule's labels, writes part of
it. A skill should treat it like any other tool result.

The run keeps the first 64 KiB. A longer body is cut, and the run is told so.
The body is stored with the run and shown on its **Input** tab. Do not send
secrets in it.

### Ignore requests and repeats

A sender may notify again while an event lasts, and again when it ends. Add a
`webhook` block so these do not each start a run:

```yaml
jobs:
  incident-triage:
    name: Incident triage
    skill: .agents/skills/incident-triage/SKILL.md
    triggers: [webhook]
    webhook:
      skip_when:
        status: resolved
      repeat_key: incident.id
      repeat_window: 2h
```

- `skip_when` ignores a request when every listed field has the listed value.
- `repeat_key` names the field that identifies an event. A request with the same
  value is ignored while that event's run is active, and for `repeat_window`
  after it finished.
- `repeat_window` is a number followed by `m`, `h` or `d`, up to `7d`. It is
  `1h` when left out.

Fields are the sender's own. Use a dot for a nested field, such as
`labels.team`. A request whose body is not JSON, or lacks the field, is never
ignored by these rules. The setup guide for each sender lists the fields to use.

### What happens to a request

A request starts a run when all of these hold:

1. It carries the secret. Otherwise it gets 401.
2. The trigger is on and the job still names it. Otherwise it gets 409.
3. The job's `webhook` settings do not ignore it. An ignored request gets 200,
   so the sender does not retry it.
4. The trigger has started fewer runs in the last hour than its cap. The cap is
   10 by default. An owner or admin can change it on the job page. Otherwise
   the request gets 429.
5. The job is ready and has no active run. Otherwise it gets 409.

The job page lists recent requests and what happened to each. It keeps the
newest 100 of each outcome. A failed sign-in is listed at most once a minute.
Turning the trigger off or rotating the secret applies from the next request.

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
3. Sync the committed manifest. Approve the job's notification access if the
   connection requires approval.
4. Start a run with **Run now**. Enable the job when you want it to run on schedule.

The server posts the run status, report summary, and a link to the run in
SecondShift after the run finishes. Failed runs are sent too, with the reason
they failed. Delivery happens outside the offline sandbox.

### Webhook payload

After each run, the server sends one `POST` with a JSON body like this:

```json
{
  "event": "run.finished",
  "text": "✅ Weekly review succeeded · acme/web\n\nFound 2 stale setup steps in README.md. Details are in the report.\n\n<https://secondshift.example.com/runs/128|Open run #128>",
  "run": {
    "id": 128,
    "status": "succeeded",
    "summary": "Found 2 stale setup steps in README.md. Details are in the report.",
    "commit_sha": "8f3c9d28f3c9d28f3c9d28f3c9d28f3c9d28f3c9",
    "scheduled_for": "2026-10-26T09:00:00+00:00",
    "finished_at": "2026-10-26T09:04:00+00:00",
    "url": "https://secondshift.example.com/runs/128"
  },
  "job": { "key": "weekly-review", "name": "Weekly review" },
  "repository": { "full_name": "acme/web" }
}
```

- `status` is `succeeded`, `failed` or `canceled`.
- `summary` is the start of the report, up to 4,000 characters. For a failed
  run it says why the run failed. The full report stays in SecondShift.
- `text` is the same summary with the status and a link, ready for chat.
- `url` opens the run in SecondShift. Opening it needs a sign-in.
- No secrets, connection details, tool calls or file contents are sent.

**Send test** posts a smaller message instead, with `event` set to
`connection.test`, a `text` line and the `binding`.

The webhook form in the dashboard shows the same sample under
**What gets sent**.

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
owner connects the service and chooses which tools any job may use. If the
connection has **Require approval for each job** turned on, the owner also
approves each job's request. It is off for new connections: then a job that
asks only for allowed tools is ready after sync. See
[who grants what](security-model.md#who-grants-what).

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

A job with an optional binding (`required: false`) still runs without it. The
run prompt then names each optional binding the run does not have, with a short
reason: not connected, waiting for approval or denied. The skill can say a
source was not checked, instead of leaving it out. The run's **Input** tab shows
the same list.

### What a binding can connect to

| Connection | Tool names | Sign-in | Scope |
| --- | --- | --- | --- |
| GitHub (built in) | `search_pull_requests`, `pull_request_read`, `list_commits`, `get_commit`, `actions_list`, `actions_get`, `get_job_logs` | Fine-grained personal access token | The job's own repository |
| Sentry (built in) | `find_projects`, `search_issues`, `search_errors`, `search_traces`, `get_sentry_resource` | Auth token with read scopes | The organization or project chosen when connecting |
| Custom MCP server | The server's own tool names | OAuth sign-in, or a bearer token | Whatever the credential can reach |
| HTTP API | The `operationId` of each operation in its OpenAPI 3 spec | Bearer token, or a named header | Whatever the credential can reach |

GitHub and Sentry offer only read tools that SecondShift allowlists. GitHub
Actions tools can list and read workflows, runs, and jobs, and read job logs as
text. Logs are capped at the last 300 lines. Triggering runs, artifacts, and
log download links are not available. The token needs read-only Actions access
for these tools. For a
custom MCP server, the owner chooses tools after connecting, and tools the
server marks read-only start selected. For an HTTP API, GET operations start
selected. The access review marks any requested tool that may change data.

[Vendor MCP servers](mcp-recipes.md) has examples for vendors that run an
official MCP server.

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

The owner can also restrict a tool's arguments for one job, for example to
post only to certain Slack channels. The repository cannot set restrictions.
Write the skill so it stays within them: a call that breaks a restriction fails
the run. See [tool restrictions](security-model.md#tool-restrictions).

An approved tool can still be misused by a prompt injection. See
[remaining risks](security-model.md#remaining-risks) before approving tools
that change data.

See the [engineering briefing example](../examples/engineering-briefing/README.md)
for a working job that reads live GitHub data.

Need a service that does not fit these options?
[File an issue](https://github.com/piotrekno1/second-shift/issues/new) describing
the service and workflow you want to automate.
