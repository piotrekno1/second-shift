# Backend ownership

| App | Owns |
| --- | --- |
| `repositories` | Repository sources, revisions, mirrors and run snapshots, sync triggers |
| `jobs` | Manifest interpretation, job reconciliation, capability requests, grants, execution-plan compilation, schedule registration |
| `triggers` | Job triggers, their secrets, the public endpoint that starts runs, received requests |
| `runs` | Task assignments, execution records, audit events, agent execution, durable workflows, recovery, artifacts, retention, report delivery |
| `connections` | Connection lifecycle, encrypted credentials, OAuth sign-ins, GitHub source connections (App installations and tokens), provider adapters for MCP, HTTP APIs, models and webhooks |
| `runner` | The isolated service's sandbox sessions, runtime enforcement, command execution, shared protocol and client |
| `gateway` | Model proxy, run tokens, model usage |
| `mcp_server` | The `/mcp` endpoint, MCP tool adapters, tool call audit and rate limit |
| `workspaces` | Membership, workspace selection, authorization, API tokens and workspace settings endpoints |
| `core` | Account and application infrastructure, cross-domain dashboard overview |

`secondshift/github` is a plain package, not an app. It holds GitHub App
credentials, tokens, the REST client, git transport and repository lookups.
It takes plain values such as an installation ID and imports no Django apps,
which its own ruff configuration enforces. `repositories.github_access` turns a
repository and its source connection into one token and a checked identity for
each operation. Syncs, checkouts, publishing and the GitHub MCP adapter all use
it.

Each HTTP app uses `api.py` for endpoints, `schemas.py` for its transport
contracts, and `serialization.py` for response builders when needed.
`config/api.py` composes the public routers. Domain modules do not import HTTP
schemas or route registration.

## Jobs and runs

Jobs are reusable definitions. A task records one assignment, and a run records
an execution attempt. Job-backed tasks retain their original job relationship;
standalone tasks own a repository directly. Each accepted start creates a task and its initial run atomically; refused starts and skipped cron
ticks create neither. Task snapshots preserve the assignment and commit at
submission, while run snapshots and execution plans remain the execution
source of truth. Webhook payload metadata is included before submission commits.
MCP tasks record the token's user and MCP origin; their runs retain the existing
manual start label. Scheduled and webhook tasks have no submitting user.

The nullable `Run.task` link preserves compatibility with older writers.
Generated migrations backfill one task per historical run from recorded inputs,
never from current job definitions; unknown origins and users remain unknown.
The versioned backfill operation lives in `runs.migration_helpers` so the data
migration can be generated with Django's migration writer. Retention removes a
task when pruning its last remaining attempt. Task and run relationships to jobs,
public responses, and the worker's execution inputs remain compatible.

Repository synchronization obtains configuration at a revision and calls
`jobs.sync`. The jobs app validates the manifest and reconciles definitions and
capability requests. Connections make external services available. Job grants
authorize their use. `jobs.planning` freezes effective policy for a future run.
It does not import the agent execution framework.

`build_execution_plan(job)` adapts persisted job definitions and their own grants
into the shared plan compiler. `build_skill_execution_plan(repository, definition,
key=..., commit_sha=...)` plans a standalone repository skill using the same
validated definition shape, without creating jobs, capability requests or
grants. Planning does not register schedules or triggers from that definition.
The caller must resolve the requested revision to a full commit SHA; the plan
records that commit rather than the repository's last synced configuration SHA.
Skill-file existence and commit availability are checked by the source workflow,
not by the planner.

Standalone definitions name only logical bindings and tools. The planner resolves
connections within the repository's workspace and uses the same automatic
approval checks as manifest sync. Connections requiring approval remain blocked;
existing job grants are never borrowed. Optional unavailable capabilities do not
block other tools. Workspace execution selection and limits apply, while runner
policy stays server-owned. The version-5 plan format and saved-job entry point
remain compatible. Standalone submission uses this planner through `runs.submissions`. Both the
session-authenticated `POST /api/tasks` and MCP `submit_task` tool require the
existing run permission, resolve repositories in the current workspace, and
record the authenticated submitter and origin. Source resolution uses the
followed branch by default or an explicit branch/full commit, verifies a regular
committed skill file when selected, and freezes the SHA, definition and per-run input. No Job
or JobGrant rows are created.

`TaskManifest` extends the existing job definition with an optional skill path.
Saved repository jobs still require a skill. Standalone tasks without a skill
require nonblank instructions, execute those instructions directly, and capture
input without reading a nonexistent file. Both instruction sources use the same
planner, server-owned sandbox and connection consent checks.

A repository-scoped idempotency key returns the original attempt for an identical
request from the same user; conflicting reuse is refused. Admission is checked
under the repository row lock before and after source access. Standalone tasks
queue up to the server-owned per-repository limit (100 unfinished tasks by default).
Only one standalone run per repository is handed to DBOS at a time; saved jobs
retain their existing per-job overlap behavior. Optional timezone-aware
`not_before` freezes the earliest start in the existing `scheduled_for` fields.
The oldest eligible task runs first; future tasks do not block eligible work.
Task and run creation are atomic and enqueue is attempted after commit. A queued
run without a workflow ID acts as a persistent outbox: the worker retries it on
each pass (15 seconds by default), using its deterministic workflow ID to recover
an interrupted handoff. DBOS outages keep standalone submissions queued rather
than failing acceptance. Repository availability and the submitter's current
role are checked at dispatch and immediately before execution.
Queued standalone tasks can be canceled through HTTP or MCP; cancellation and
execution take the same repository lock. A canceled workflow exits before source
checkout or sandbox creation. Existing workflow and step names remain unchanged,
including replay of older start steps that returned no value.
Deploy the control plane and workers together before using queued cancellation:
older workers do not have the pre-execution cancellation guard.
The optional global DBOS concurrency limit supplements the existing per-worker
limit across all run origins; its default leaves existing deployments unchanged.
New runs use the existing durable worker,
artifact delivery and retention flow. Ad hoc tool calls and notifications recheck
that the connection still permits automatic access. Run history, authorization,
model usage and subscription caps include standalone runs. Existing job runs
retain their source lookup and job-specific grant checks; their response values
remain unchanged. Standalone responses have a null `job_id` and expose the task's
name in the existing display field. The generated migration preserves existing
rows and gives the new submission-key column a database default so earlier
job-task writers can still insert. Upgrade the control plane and workers before
submitting standalone tasks: older binaries assume every run has a job.

A job runs one run at a time. Run now and webhook triggers go through
`runs.services.start_run`, which refuses while a run is active and checks
readiness first. Cron goes through `runs.services.create_scheduled_run`, which
skips the tick while a run is active and notes the skip on that run. A replayed
tick returns the run it already created. Readiness for cron runs is checked when
they execute. Existing execution plans and
input snapshots remain attached to their run. Runtime checks still enforce
connection ownership and revocation.

The runs app has three internal packages:

- `execution`: agent construction, workspace and external tools, budget and
  deadline enforcement, model gateway access, tool authorization at call time.
- `orchestration`: DBOS registration, dispatch, workflows, reconciliation and
  periodic worker tasks.
- `reporting`: history queries, failure summaries, health, connection usage and
  report delivery payloads.

`runner.client` is the public client for sandbox operations. Runs consume that
client and the shared protocol. Runtime flags, images, mounts and isolation
remain enforced by the runner service. No additional sandbox Django app exists.

## Database migrations

First-party migration history starts from the current app structure. Each
model-owning app has generated initial migrations, and tables use their owning
app's name. The obsolete `automations` and `integrations` apps have been removed.
The control plane and runner have independent migration graphs and databases.

This alpha reset does not support upgrading a database created with the previous
migration history. Back up existing data before switching versions, then create
fresh control-plane and runner databases, including fresh DBOS workflow state.
Do not fake the new initial migrations against an old database: its tables and
content types belong to the previous app structure. Connections, approvals and
schedules must be configured again.

For a new control-plane database, run `python manage.py migrate`. For a new
runner database, use the runner's environment and settings:

```bash
python manage.py migrate --settings=secondshift.config.settings.runner
```

Public API URLs and payloads stay stable. Admin URLs and generated OpenAPI tags
and operation IDs reflect the new app ownership. DBOS workflow names, step names,
schedule names, workflow IDs and the durable agent name remain unchanged.
