# Backend ownership

| App | Owns |
| --- | --- |
| `repositories` | Repository sources, revisions, mirrors and run snapshots |
| `jobs` | Manifest interpretation, syncing jobs from repositories, capability requests, grants, the execution plan, schedule registration |
| `triggers` | Job triggers, their secrets, the public endpoint that starts runs, received requests |
| `runs` | Tasks, runs, their events, agent execution, durable workflows, recovery, artifacts, retention, report delivery |
| `connections` | Connection lifecycle, encrypted credentials, OAuth sign-ins, GitHub source connections, adapters for MCP, HTTP APIs, models and webhooks, outbound HTTP rules |
| `runner` | The isolated service's sandbox sessions, runtime enforcement, command execution and its client |
| `gateway` | Model proxy, run tokens, model usage |
| `mcp_server` | The `/mcp` endpoint, MCP tool adapters, tool call audit and rate limit |
| `workspaces` | Membership, workspace selection, authorization, API tokens and workspace settings |
| `core` | Account and application infrastructure, the dashboard overview |

Apps depend on each other in one direction: config, github, workspaces,
connections, repositories, jobs, gateway, runs, triggers, mcp_server, core. A
domain module imports only apps before its own. The HTTP and composition files
(`api.py` or an `api/` package, `schemas.py`, `serialization.py`, `admin.py`, `urls.py`, management
commands and MCP tools) may import any app.

`secondshift/github` is a plain package, not an app. It holds GitHub App
credentials, tokens, the REST client, authenticated git and repository lookups. It
takes plain values and imports no Django apps. Its own ruff configuration
enforces that. `repositories.github.access` turns a repository and its source
connection into a token and a checked identity for each operation.

`secondshift/git` is a plain package, not an app. Every process runs git
through `git.commands`. It turns off system and global settings, hooks and the
file system monitor, and passes git only a small environment. A failure raises
`GitError`, whose message never holds git's own output. Callers turn it into
their own error. The package imports nothing else from SecondShift, so the
runner and the control plane may both use it. `core/tests/test_runner_boundary.py`
checks this.

`secondshift/docker` is a plain package, not an app. It answers the questions
several parts ask Docker: whether it runs, whether it is Docker Desktop, and
which image ID a tag names. It also builds the agent image. A failure raises
`DockerError`. The runner still builds its sandbox command lines itself, next
to the checks that keep repository input out of them. Like `git`, the package
imports nothing else from SecondShift.

`secondshift/runner_protocol` is a plain package, not an app. It holds what
the runner and the control plane agree on: session and command specs, agent
requests and results, agent backends, sandbox paths and size limits, the
layout of a collected archive, the gateway's agent broker path, the runner's
HTTP client, and encryption to the runner's provisioning key. It imports
neither side, so both settings modules can use it too. The runner imports no
control plane code. The control plane reaches the runner only through this
package. `core/tests/test_runner_boundary.py` checks this.

`secondshift/agent` is a plain package for the agent image, the container that
runs a subscription CLI for one run. `agent/image` holds the files the image
ships. They use only the standard library, because the image has nothing else
installed. Each CLI has its own package, `image/claude` and `image/codex`, with
its command, its event reader and its config files. Config files are real
files. `outcomes.fill_template` puts each run value in as a JSON literal and
parses the result, so a value can never add a setting. The runner starts the image, and the control plane reads the
outcomes and usage it reports. `agent/build.py` builds the image on this
machine for `secondshift up` and development.

Each HTTP app uses `api.py` for endpoints, or an `api/` package with one module
per area, like `connections/api/`. `schemas.py` holds its contracts and
`serialization.py` for response builders. `config/api.py` composes the public
routers. Every query on an app's models lives in that app's `dao.py`.
`core/tests/test_dao_boundary.py` checks this.

## Jobs, tasks and runs

A job is a reusable definition synced from a repository. A task is one
assignment in a repository. A run is one attempt at a task. Every run has a
task, and every task belongs to a repository. A job run's task records who
started it. An ad hoc task also records its idempotency key and a fingerprint
of what was submitted, so a repeated submission finds its first run.

`Run.started_by` records what started a run: a schedule, a person, a webhook or
an MCP client.

`jobs.repository_sync` reads a repository's configuration at a revision and
reconciles its jobs and capability requests. Git work happens outside database
transactions.

## The execution plan

`jobs.execution_plan.ExecutionPlan` is the frozen policy for one run. The planner builds
it once from server-owned settings, grants and connections, and the run stores
it. Runs, brokers and pages read it through `Run.plan`. It never holds
credentials.

`build_execution_plan(job)` plans a saved job from its grants.
`build_skill_execution_plan(repository, definition, key=..., commit_sha=...)`
plans an ad hoc skill with the same compiler. It creates no jobs or grants, and
uses only connections that grant access without review. The caller resolves
the revision to a full commit SHA first.

## Skills from another repository

`repositories.external_skills` fetches a skill from another GitHub repository
and pins it to a commit. A public repository is fetched without credentials
into a shared cache. A private one is fetched with a token from the
workspace's GitHub connections, into a cache keyed by workspace. GitHub decides
which it is on every resolve and snapshot, never the cache. Its folder becomes a
snapshot of its own. A job's `skill_source` must name a full commit SHA. A
task may name a branch or tag, and submission pins it. The plan records the
source in `JobPlan.skill_source`. The run uploads the skill snapshot beside the
repository's, and the runner mounts it read-only at `/skill`.

`jobs.skill_needs` reads the MCP tools a skill names, for people to review
before a run. It grants nothing. During a run, the agent can record access it
needed with `report_missing_access`. Those land in `RunAccessNeed`.

## Starting and queueing runs

`runs.services.start_run` starts a job run now or from a webhook. It refuses
while the job has a run in progress and checks readiness first. Cron goes
through `create_scheduled_run`, which skips the tick while a run is active.

`runs.submissions.submit_skill` accepts an ad hoc task over HTTP or MCP. It
resolves the source, plans it and creates the task and its run in one
transaction. Ad hoc tasks queue per repository and run one at a time, oldest
first. A queued ad hoc run without a workflow ID is an outbox: the worker hands
it to DBOS on each pass, so a DBOS outage delays it instead of failing it. A job
run whose enqueue fails is marked failed at once.

## Running

A run's inputs beyond its plan are typed as `runs.inputs.RunInput`: an ad hoc
task's definition and input, a trigger's payload and repeat key, and the earlier
runs it reads. What the sandbox leaves behind is `runs.outputs.RunOutputs`. Read
them through `Run.inputs` and `Run.collected`.

`runs.execution.consent.approved_connection` decides whether a run may still
use a connection its plan names. A job's runs need the owner's grant. Ad hoc
runs need a connection that grants access without review. Tool calls and
notifications both ask it.

A failed run records a `RunFailure` kind and a reason. Pages and queries read
these fields. The summary is text for people.

`runs.runners` holds the runners registered with `secondshift runners`. The
worker keeps the configured runner registered as `default`. The
`start_runner_session` step pins the run to a runner with `Run.runner` before
uploading anything, while it holds the run's row lock. A retried step finds the
saved runner and reaches it again. Every later call for the run, including
gateway tool calls, goes through `runner_client_for(run_id)`. A run with no
runner, which can only be one that started before the upgrade, uses the
configured runner. The environment list on pages still asks the configured
runner.

Each subscription login is sealed to the provisioning key of the runner that
uses it, and that runner caches a copy. Any runner may hold one, from a run it
took or a check it ran. A disconnect or reconnect queues an
`AgentCredentialCleanup`, which asks every registered runner to remove its copy.
`removed_from` records the runners that confirmed. A runner that is down is not
asked until it comes back, but the cleanup stays until it confirms.

Each login has a home runner, `runs.LoginHome`, set the first time a run or a
check uses it. Its runs and checks go only there while it is active and offers
the run's environment, and wait when it is full. Codex refreshes its tokens
into that runner's copy and the old refresh token stops working, so the home
keeps the only live copy. Its file lock lets one run use the login at a time.
When the home drains, goes down or lacks the environment, the login moves, and
Codex may need a reconnect. A reconnect issues a new key, and so a new home.

Removing a runner first asks it to delete every login copy it could hold: each
login's current key and the replaced keys still waiting for cleanup. A runner
that can't confirm is kept unless the operator forces it, and then its
`agent-auth` directory has to be deleted on the host.

Placement chooses among the active runners in the `default` pool. Runners in
other pools take no runs until pools are routed. A runner qualifies when it
offers the run's environment and has fewer unfinished runs pinned to it than
its `max_sessions`. Among those, the one with the most free sessions wins. The
worker asks each runner for its environments every minute. A runner that has
never answered may take any environment, and it refuses a session it can't
run. Choosing locks the run and the candidate runners, always in the same
order, so two workers never both take a runner's last session. When every
runner that could take the run is full or draining, the step waits in short
transactions until the run's time limit. When no runner offers the run's
environment, the run fails at once. Placement stays inside
`start_runner_session` and never becomes a step of its own, because
workflows recover across releases and a new step would shift their step
numbers.

The runs app has three internal packages:

- `execution`: agent construction, workspace and external tools, budget and
  deadline enforcement, model gateway access, consent checks at call time.
- `orchestration`: DBOS registration, dispatch, workflows, reconciliation and
  periodic worker tasks.
- `reporting`: failure summaries, health, connection usage and report payloads.

`runner.client` is the public client for sandbox operations and subscription
agent runs. Runtime flags, images, mounts and isolation stay with the runner
service. `sandbox_flags()` gives every sandbox container the same hardening and
user.

`runs.services.create_attempt` creates every run, for a job or an ad hoc task,
with the deliveries its plan promises shown as pending.

## Model providers

`connections.model_providers.MODEL_PROVIDERS` holds what each provider is: its
API family, default URL, and whether it routes other vendors' models. The
gateway serves each `ModelFamily` from the routes in `gateway.proxy.ROUTES`.

## Outbound HTTP

Calls to a service a person configured go through
`connections.outbound.outbound_client` or `outbound_sync_client`. They connect
only to addresses checked at connect time, ignore proxy settings, follow no
redirects and bound the response size.

## Database migrations

The control plane and the runner have separate migration graphs and databases.
Migrations are generated, never written by hand. A data migration calls a
versioned helper in the app's `migration_helpers.py`, since it runs with
historical models. Before a release, the migrations added since the last one
are squashed into one per app.

For a new control-plane database, run `python manage.py migrate`. For a new
runner database, use the runner's settings:

```bash
python manage.py migrate --settings=secondshift.config.settings.runner
```

DBOS workflow names, step names, schedule names and workflow IDs stay stable
across releases.
