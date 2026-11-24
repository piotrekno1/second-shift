# Proposal: multiple runners per control plane

Status: draft · November 2026

## Summary

Today one control plane talks to exactly one runner. This proposal lets an
operator register several runners and have the control plane place each run
on one of them, pin every later call for that run to the same runner, and
respect each runner's capacity. Runners stay unaware of each other. All
placement lives in the control plane, inside the existing durable workflow.

## Motivation

- **Throughput.** A single host's CPU, memory and Docker limit how many
  sandboxes run at once. Adding workers does not help, because every worker
  sends its sandboxes to the same runner.
- **Isolation tiers.** Operators may want separate hosts for different trust
  levels or hardware, such as a GPU sandbox, or a host reserved for one
  workspace.
- **Maintenance.** Draining one runner for an upgrade or a host reboot should
  not stop all execution.

## Current state

Workers already scale out. DBOS workers share one queue, limited by
`SECONDSHIFT_RUN_CONCURRENCY` per worker and
`SECONDSHIFT_RUN_GLOBAL_CONCURRENCY` overall
(`runs/orchestration/dbos_runtime.py`).

The runner is a singleton:

| Assumption | Where |
| --- | --- |
| One runner address and token | `RUNNER_SERVICE_URL`, `RUNNER_SERVICE_TOKEN`. `runner_protocol.client.get_runner_client()` takes no arguments. |
| Session calls carry only a session ID | `start_runner_session`, `run_setup_command`, `execute_runner_command`, `collect_run_outputs` and `stop_runner_session` steps. `AgentLease.session_id` holds the ID the broker uses for gateway tool calls. |
| Runner state is local to its host | `RunnerSession` and `RunnerCommand` live in a runner-only database (SQLite by default). Snapshots and workspaces live under `RUNNER_WORK_ROOT`, and containers run on that host's Docker. |
| One provisioning key | Subscription logins are sealed to the runner's public key (`runner_protocol.credentials`). |
| One cache of logins | The runner caches logins by `credential_key`. `connections.agents.remove_cached_credentials` deletes from one runner. |
| One environment list | `jobs.environments` caches one runner's `GET /v1/environments`. |
| No runner capacity | The runner accepts every session. Only worker concurrency bounds load. |

These assumptions also rule out the simplest option: putting a load balancer in
front of several runners. A session's setup, commands, collection and delete
must reach the host that created it.

## Goals

- Run many sandboxes across several runner hosts behind one control plane.
- Keep a run on one runner for its whole life.
- Place runs by sandbox environment and free capacity, and queue them when no
  runner has room.
- Drain a runner without failing its in-flight runs.
- Detect a dead runner and settle its runs instead of leaving them stranded.
- Keep today's single-runner install working with no configuration change.

## Non-goals

- Moving a live session between runners. A sandbox's state stays on its host.
- Letting repository content choose a runner, a host or a pool.
- Autoscaling runner hosts. That remains the operator's job.
- Sharing a runner between control planes.

## Design

### Runner registry

Add a `Runner` model, owned by the `runs` app because runs own execution. A
`runners` app is an alternative if the surface grows. The model has these
fields:

- `name`: a stable slug, shown to operators
- `url`
- `token`: encrypted, one per runner
- `provisioning_public_key`: fetched from the runner
- `environments`: the names it offers, refreshed from `GET /v1/environments`
- `pool`: an operator label, default `default`
- `max_sessions`
- `state`: `active`, `draining` or `down`
- `last_seen_at`

Runners are registered by the operator, from a management command
(`secondshift runners add|drain|remove`) or from config. On upgrade, a data
migration seeds a runner named `default` from `RUNNER_SERVICE_URL` and
`RUNNER_SERVICE_TOKEN` when they are set, so existing installs behave the
same. The migration copies only references. The token is encrypted the same way
as connection credentials.

Each runner keeps its own token. A compromised runner then cannot call another
runner, and revoking one does not touch the rest.

The runner reports `max_sessions` (a new `SECONDSHIFT_RUNNER_MAX_SESSIONS`, set
from its CPU and memory caps) in a small `GET /v1/info` response alongside the
environments and the public key. The control plane stores the value at refresh
time. An operator may set a lower limit.

### Placement

Add a durable step, `secondshift.assign_runner`, at the start of
`execute_run`, before `prepare_source` and `start_runner_session`:

1. In `transaction.atomic()`, lock the run row. If `run.runner_id` is already
   set, return it. A replay after a crash then lands on the same runner.
2. Lock the candidate runner rows (`select_for_update(skip_locked=False)`, in a
   fixed order) and choose an `active` runner in the job's pool that offers the
   run's environment and has fewer active runs than `max_sessions`. Prefer the
   one with the most free slots. Ties go to the lowest ID, so tests stay
   deterministic.
3. Save `run.runner_id` and commit.

The DAO counts active runs per runner, using a new index on
`(runner_id, status)`. The decision rules (state, pool, environment and the
tie-break) stay in the service, as AGENTS.md requires.

If no runner fits:

- **Capacity is full.** The step raises a retryable "no capacity" error with
  backoff, so the run stays queued and is not failed. A cleaner option is to
  keep the DBOS global limit at or below total capacity, so this path is rare.
  See [Concurrency](#concurrency).
- **No runner offers the environment.** The run fails with a clear
  `RunFailure` kind, as `environment_missing` does today.

### Routing calls

Replace `get_runner_client()` with:

- `runner_client_for(run_id)`, which every session-scoped caller uses: workflow
  steps, `workspace_tools`, `cli_agent`, `run.py` and the agent broker.
- `runner_client(runner)`, which fleet operations use: environment refresh,
  login checks and cleanup.

Keep step names and argument lists unchanged. Steps such as
`execute_runner_command(session_id, …)` resolve the runner from the run inside
the step, not from a new argument. Workflows already in flight across the
upgrade then replay with the same recorded inputs, and they land on the
`default` runner that the migration created. To keep tool calls cheap, the
lookup is one indexed read (`runs.dao.runner_for_run`), and its result is cached
for the life of the agent loop.

`AgentLease` gains `runner_id`, which `agent_broker` uses for gateway tool calls.

### Subscription logins

- Seal each login to the assigned runner's public key when its session starts.
  `client.encrypted_auth` already happens per call, so only the key source
  changes.
- Cached logins: record which runners hold a copy of each `credential_key`,
  in a `RunnerCredentialCopy` row written when a session starts with it. On
  disconnect, `queue_cleanup` creates one cleanup per copy, and
  `retry_credential_cleanups` retries each one against its runner.
- Login checks (`check_agent_connection`) run on any active runner that offers
  the agent backend.

### Environments

`jobs.environments` changes from asking one runner to returning the union of
the active runners' registry rows. `environment_missing(name)` means no active
runner offers `name`. The refresh moves from a per-process 60-second cache to
a periodic worker task (`secondshift.refresh_runners`), which also records
`last_seen_at`. Pages read the database and never wait on a runner.

### Draining, failure and recovery

- **Drain.** Placement skips a `draining` runner, and its in-flight runs finish
  normally. A draining runner with no active runs can be upgraded or removed.
- **Health.** The periodic refresh calls `GET /-/health/`. After N consecutive
  failures (default 3, about 90 seconds) the runner becomes `down`, and it
  returns to `active` after one success.
- **Runs on a down runner.** The sandbox is gone or unreachable, so these runs
  cannot resume. A reconciliation pass in `orchestration/reconciliation.py`
  handles them:
  - It fails runs that are still active on a `down` runner and whose current
    step failed with `RunnerProtocolError`, using a new `RunFailure` kind,
    `runner_lost`.
  - Ad hoc tasks keep their existing outbox semantics. Whether to start them
    again automatically on another runner is an open question; see below.
  - Session cleanup (`stop_runner_session`) against a down runner is queued
    and retried, like credential cleanup. When the runner returns, it also
    expires its own sessions with its existing snapshot and session TTLs.
- **Retries inside a step.** `RunnerProtocolError` keeps today's
  `max_attempts`. Placement is not repeated mid-run.

### Concurrency

Keep both DBOS limits. Add a check at startup and on registry change that warns
when `SECONDSHIFT_RUN_GLOBAL_CONCURRENCY` is 0 (unlimited) or larger than the
total `max_sessions` of active runners. Placement is the real per-runner
gate. The DBOS limit only keeps workers from tying up threads on runs that are
waiting for capacity.

### Pools and routing policy

`pool` is an operator-side label. Workspace settings (owner-controlled, not the
repository) may name a pool. The repository manifest keeps choosing only a
logical `environment`. A job can never name a runner, a host or a pool through
`.secondshift.yaml`. This keeps the rule that repository input never controls
runtime placement or flags.

## Security

- No change to the runner's own enforcement. Each runner still refuses to start
  without `runsc` in production and accepts only server-owned images, mounts
  and flags.
- Per-runner tokens and per-runner provisioning keys. A login sealed to runner
  A cannot be opened by runner B.
- Session IDs are deterministic (`run-{run_id}-{runner_session_key}`), so they
  are unique across runners. Only the runner recorded on the run, which a
  repeated `start_runner_session` reaches again, ever receives calls for that
  session. Another runner answers 404 instead of acting on it.
- Repository input still cannot reach placement. Tests show a manifest can't
  pick a runner or pool, matching the runner boundary tests AGENTS.md asks for.
- Registering a runner is an operator action. It is a CLI or settings change,
  not a dashboard action available to workspace owners.

## Migration and compatibility

1. Add the schema: `Runner`, `Run.runner_id` (nullable), `AgentLease.runner_id`
   and `RunnerCredentialCopy`, from generated migrations.
2. Add a data migration that seeds `default` from the existing env vars.
   Installs without those vars, such as some dev setups, seed nothing, and the
   control plane reports "no runner registered".
3. Treat a null `run.runner_id` as `default`. This covers runs that started
   before the upgrade.
4. Keep `RUNNER_SERVICE_URL` working as the seed for `default`. Deprecate it
   once registry management is documented, and remove it a release later.
5. Local mode (`secondshift up`) keeps one runner and registers it at startup.

## Rollout plan

Each step is a separate PR that ships on its own:

1. **Registry and seed.** Add the model, migration and CLI. Behaviour is
   unchanged because everything still goes to `default`.
2. **Routing.** Add `runner_client_for(run)` and switch all callers, then put
   `runner_id` on `Run` and `AgentLease`. `assign_runner` always picks
   `default`.
3. **Placement and capacity.** Add a real choice, `/v1/info`,
   `max_sessions`, and the retry when capacity is full.
4. **Logins.** Seal per runner, record copies and fan out cleanup.
5. **Environments.** Use the union from the registry, with a periodic
   refresh.
6. **Health, drain and recovery.** Add the `down` state, the `runner_lost`
   failure and the reconciliation pass.
7. **Docs.** Update `architecture.md`, `backend-architecture.md#starting-and-queueing-runs`,
   `self-hosting.md`, `production-deployment.md` and
   `environment-variables.md`, and extend the production end-to-end suite to two
   runners.

## Testing

- **Placement:** environment filter, pool filter, capacity, tie-break, and that
  a draining or down runner is never chosen.
- **Durability:** a crash after `assign_runner` commits and before DBOS records
  the step replays to the same runner. A crash after `start_runner_session`
  succeeds never opens a second session on another runner.
- **Concurrency:** two workers placing at once never go over `max_sessions`,
  tested against PostgreSQL row locks.
- **Recovery:** a runner goes down mid-run, the run ends `runner_lost`, and
  session cleanup is retried after the runner comes back.
- **Logins:** a login sealed for A is refused by B, and disconnecting removes
  every copy.
- **Boundary:** manifest fields can't influence placement, and the runner
  boundary test still passes.
- **Upgrade:** a workflow in flight from before the migration finishes on
  `default`.

## Alternatives considered

- **A load balancer with sticky sessions.** It hides the runner choice from the
  control plane, so placement can't consider environment or capacity, drain is
  invisible, and the stickiness key has to be invented in the session ID. It
  also fails silently when the load balancer reshuffles.
- **A shared runner database and shared workspace storage.** It makes runners
  interchangeable for metadata only. Containers and Docker sockets are still per
  host, so routing comes back anyway, and a shared filesystem crosses the
  isolation boundary between hosts.
- **Runners pulling work from a queue.** Runners would ask for runs instead of
  being told. That inverts the trust direction: runners would need access to
  the control plane's queue and run state, which today they don't have.
- **One worker per runner.** Pinning each worker to a runner by config is
  simpler, but it couples two separate scaling axes, needs per-worker queues
  to route by environment, and still leaves the gateway's tool calls without a
  route.

## Open questions

1. When a runner is lost, should ad hoc tasks start again on another runner
   automatically, or fail and let the caller resubmit? Job runs probably just
   fail and wait for their next trigger.
2. Should pools be chosen per workspace, per job (by the workspace owner), or
   both?
3. Is `max_sessions` alone enough, or does placement need weights for CPU and
   memory per environment, for example when a large sandbox counts as two
   slots?
4. Should runner registration also support runners that register themselves
   with a one-time join token, for operators adding hosts often?
