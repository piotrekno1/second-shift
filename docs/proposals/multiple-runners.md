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
- `images` and `default_environment`: the rest of that answer, for the
  settings page
- `pool`: an operator label, default `default`
- `max_sessions`
- `state`: `active`, `draining` or `down`
- `failing_since`: when its checks started failing

Runners are registered by the operator, from a management command
(`secondshift runners list|add|drain|activate|remove`). The worker keeps a
runner named `default` registered from `RUNNER_SERVICE_URL` and
`RUNNER_SERVICE_TOKEN` when they are set, so existing installs behave the same.
This is the first of the worker's periodic tasks, so it also retries if the
database is not up yet. Settings own `default`'s URL and token. The operator
owns its state, pool and capacity, so a drain survives restarts. Tokens are
encrypted the same way as connection credentials. This is not a data migration,
because migrations must not read service env vars, which differ by role, and
migrations here are generated only.

Each runner keeps its own token. A compromised runner then cannot call another
runner, and revoking one does not touch the rest.

The operator sets `max_sessions` with `secondshift runners add
--max-sessions`. A runner-reported limit (a `GET /v1/info` with
`SECONDSHIFT_RUNNER_MAX_SESSIONS`) can come later if operators want it. The
worker asks every runner that isn't down for `GET /v1/environments` once a
minute, and stores the names in `Runner.environments`.

### Placement

`runs.runners.assign_runner(run_id)` runs at the top of the existing
`secondshift.start_runner_session` step, before any upload. It is not a step of
its own, because a new step would shift DBOS step numbers in workflows already
in flight.

1. In `transaction.atomic()`, lock the run row. If `run.runner_id` is already
   set, return it. A replay after a crash then lands on the same runner.
2. Lock the candidate runner rows (`select_for_update(skip_locked=False)`, in a
   fixed order) and choose an `active` runner in the job's pool that offers the
   run's environment and has fewer unfinished runs than `max_sessions`. Prefer the
   one with the most free slots. Ties go to the lowest ID, so tests stay
   deterministic.
3. Save `run.runner_id` and commit.

The DAO counts active runs per runner, using a new index on
`(runner_id, status)`. The decision rules (state, pool, environment and the
tie-break) stay in the service, as AGENTS.md requires.

If no runner fits:

- **Every runner that could take it is full or draining.** The step waits in
  place, checking again in short transactions every few seconds, up to the
  run's time limit. A DBOS retry would fail the run after a few attempts, and
  a new step can't be added. Keeping the DBOS global limit at or below total
  capacity makes this path rare. See [Concurrency](#concurrency).
- **No runner offers the environment, or the pool is empty.** The run fails
  at once with a clear reason.

Until pools are routed (open question 2), only the `default` pool takes runs.

### Routing calls

Replace `get_runner_client()` with:

- `runner_client_for(run_id)`, which every session-scoped caller uses: workflow
  steps, `workspace_tools`, `cli_agent`, `run.py` and the agent broker.
- `runner_client(runner)`, which fleet operations use: environment refresh,
  login checks and cleanup.

Keep step names unchanged. `execute_runner_command` and `stop_runner_session`
take the run ID first, so they can find the run's runner. DBOS checks only the
recorded step name on replay, so workflows already in flight across the upgrade
replay correctly. A run with no runner, which can only be one that started
before the upgrade, uses the configured runner. The lookup is one read of the run
with its runner.

The broker gets the run from the lease, so `AgentLease` needs no runner of its
own.

### Subscription logins

- Seal each login to the assigned runner's public key when its session starts.
  This holds since routing (#485), because `cli_agent` asks the run's runner
  for its key.
- Cached logins: a disconnect or reconnect asks **every** registered runner to
  remove its copy, instead of tracking which runners hold one. Tracking could
  miss copies: a login check caches one too, and a crash between the session
  start and a tracking write would leave one untracked. The runner's DELETE is
  safe to repeat. `AgentCredentialCleanup.removed_from` lists the runners that
  confirmed. A runner that is down is not asked, but the cleanup stays until it
  confirms.
- Each login has a home runner (`runs.LoginHome`). Its runs and checks go only
  there while it is active and offers the environment. Codex refreshes its
  tokens into the runner's copy and the old refresh token stops working, so
  spreading a login across runners would break it on all but one. The login
  moves only when its home drains, goes down or lacks the environment.
- Removing a runner first asks it to delete every login copy it could hold,
  and refuses unless forced when it can't confirm.

### Environments

`jobs.environments` changes from asking one runner to reading the registry
rows of the runners in the `default` pool. An environment is missing when none
of them offers it. Drained and unanswering runners still count, because
placement waits for them rather than failing the run. A runner that has never
answered may offer anything, as in placement. The refresh moves from a
per-process 60-second cache to a periodic worker task
(`runner_health.refresh_runners`), which also records `failing_since`. Pages
read the database and never wait on a runner. A list of jobs reads the runners
once.

The settings page shows each runner's images and default. When every runner
offers the same, it shows them once, as it does for one runner today.

### Draining, failure and recovery

- **Drain.** Placement skips a `draining` runner, and its in-flight runs finish
  normally. A draining runner with no active runs can be upgraded or removed.
- **Health.** The worker's minute-by-minute refresh is the health check. A
  runner that doesn't answer gets `failing_since`, which clears at its next
  answer. Health is kept apart from `state`: if it flipped `state` to `down`
  and back to `active`, a runner the operator drained would be reactivated
  when it came back.
  - **Clocks.** Health never reads a time from a runner, so a runner's clock
    can be off. `failing_since` is stamped by the database (`Now()`), and the
    thresholds compare against the database's time, not the host's. The web
    app and the worker may run on different hosts, and skew between them
    would otherwise shift both thresholds by that amount.
  - After 2 minutes of failing, placement and login routing skip the runner,
    and logins whose home it is move.
  - After 10 minutes, the refresh fails its active runs with the `runner`
    failure. The workflow may still be blocked on the runner. When that call
    ends, `finish_run` finds the run finished and changes nothing.
  - A step that can't reach its runner raises `RunnerUnreachable`, so a run
    that fails on its own is also labelled `runner`.
  - The runner has no session expiry of its own, so a lost run's session goes
    into `SessionCleanup` and is stopped when the runner answers again.
  - Ad hoc tasks keep their existing outbox semantics. Whether to start them
    again automatically on another runner is an open question; see below.
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

1. Add the schema: `Runner`, `Run.runner` (nullable) and
   `AgentCredentialCleanup.removed_from`, from generated migrations.
2. The worker registers `default` from the existing env vars. Installs without
   those vars, such as some dev setups, register nothing, and the control plane
   reports "no runner registered".
3. Treat a null `run.runner_id` as `default`. This covers runs that started
   before the upgrade.
4. Keep `RUNNER_SERVICE_URL` as the way to configure `default`. A
   single-runner install never needs the `runners` command.
5. Local mode (`secondshift up`) keeps one runner and registers it at startup.

## Rollout plan

Each step is a separate PR that ships on its own:

1. **Registry and seed** (#484). Add the model, the CLI and the worker task
   that registers `default`. Behaviour is unchanged because everything still
   goes to `default`.
2. **Routing** (#485). Add `Run.runner` and `runner_client_for(run_id)`, and
   switch every session-scoped caller. `assign_runner` always picks `default`.
3. **Placement and capacity** (#488). Choose by environment and free sessions,
   wait while runners are full, and refresh each runner's environments.
4. **Logins** (#489). Login homes, cleanup on every runner, and wiping logins
   before a runner is removed.
5. **Environments** (#492). Readiness and the settings page read the
   registry rows the worker's check fills in, and never ask a runner.
6. **Health, drain and recovery** (#491). `failing_since`, the `runner` failure,
   failing runs on a lost runner, and stopping their sessions when it returns.
7. **Docs** (#493). Update `architecture.md`, `self-hosting.md`,
   `production-deployment.md` and `environment-variables.md`, and add
   `runner_key` so an operator can compare a runner's key fingerprint.
8. **Subscription agents on another host.** Relay the agent's tool calls
   through the runner, as in the
   [addendum](#addendum-agent-tool-calls-from-another-host). Then extend the
   production end-to-end suite to two runners.

## Testing

- **Placement:** environment filter, pool filter, capacity, tie-break, and that
  a draining or down runner is never chosen.
- **Durability:** a crash after `assign_runner` commits and before DBOS records
  the step replays to the same runner. A crash after `start_runner_session`
  succeeds never opens a second session on another runner.
- **Concurrency:** two workers placing at once never go over `max_sessions`,
  tested against PostgreSQL row locks.
- **Recovery:** a runner goes down mid-run, the run ends with the `runner` failure, and
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

## Addendum: agent tool calls from another host

### Problem

Claude Code and Codex call the run's tools through the agent broker,
`POST /gateway/agent/mcp` on the gateway, with the run's lease token. On one
host, the gateway sits on the runner host's internal agent network at a fixed
address, and agents reach it there. A runner on another host has its own
agent network, which the gateway isn't on, and the agent network has no route
out except the egress proxy to the providers' hosts. So subscription runs on
such a runner can't reach their tools.

Every other call already goes from the control plane to the runner. This one
is the only call in the other direction.

### Options considered

- **A private network between hosts.** WireGuard or Tailscale, with the
  gateway published on the control plane's private address and the runner
  host's egress proxy forwarding the agents' port to it (`--forward`). This is
  mostly Compose files, plus a way for the gateway to accept the forwarded
  host name. It relies on the operator's network, and a leaked lease token is
  usable from anywhere on that network until the run ends.
- **The broker on the public address.** Caddy routes the broker path, on a
  host name of its own, and the egress proxy lets agents reach it. Anyone can
  reach the endpoint, though only a live lease token gets anything. The lease
  token is the weak point: the agent holds it and is untrusted, and could write
  it out through a tool it is allowed, such as a GitHub comment. Today a leaked
  token is useless outside the agent network. Here it would work from anywhere
  until the run ends.
- **The public address with a per-runner client certificate.** As above, but
  the runner host's proxy forwards over TLS with a key the runner generates and
  the control plane pins, as it pins the provisioning key. Caddy requires a
  client certificate, and the gateway checks its key against the run's runner.
  A leaked token is then useless without that key. It works on any network,
  but adds an internet-facing route, a second Caddy site and a header only
  Caddy may set.
- **Relaying through the runner (chosen).** The control plane already holds an
  authenticated connection to the run's runner for the agent's whole run:
  `run_agent` blocks until the agent finishes. The tool calls ride back over
  that direction instead of calling in.

### Design

```
worker ──run_agent (blocks)──▶ runner ──starts──▶ agent container
worker ──waiting calls?──────▶ runner ◀──tool call (waits)──┘
worker ──answer─────────────▶ runner ──answer──────────────▶ agent
```

- **Runner relay.** The runner listens on a second port, which serves only
  the relay. The runner host's egress proxy forwards the agents' broker port
  to it, so agents never reach the runner API. The relay accepts the same
  JSON-RPC POST as `agent_mcp`, checks the lease token against the
  `broker_token` the runner received in `run_agent`, stores the request as a
  pending call for that session, and waits for its answer. A call without an
  id, a notification, is answered `202` at once. The relay never reads MCP.
- **Pending calls.** A runner table holds the call's id, session, request
  body, answer and times. A runner serves requests from several processes, and the
  relay's wait and the worker's answer may land in different ones, so the
  database is what they share. A session's pending calls are deleted when
  it stops.
- **Runner API.** Two session-scoped endpoints behind the service token:
  `GET /v1/sessions/{id}/agent/calls` returns waiting calls, holding the
  request a few seconds while there are none, and
  `POST /v1/sessions/{id}/agent/calls/{call}` stores an answer. Both are
  async views, so waiting holds no thread.
- **Worker.** While `run_agent` blocks, `execute_cli_step` runs a thread that
  collects waiting calls through `runner_client_for(run_id)`, answers each
  with `mcp_request(lease, message)` and posts the answer. The thread stops
  when `run_agent` returns. Every check stays where it is: the lease, the
  run's status and deadline, the tool call budget and the approved tools.
- **Durability.** The thread lives inside the existing
  `secondshift.execute_cli_agent` step, so no step is added or renamed. A
  retried step refuses to start the agent again, as it does today when a
  lease exists. An answer posted after the session stopped is dropped.
- **Where it applies.** A runner whose `SECONDSHIFT_AGENT_BROKER_URL` is empty
  and whose relay is configured uses the relay. It says so in its environments
  answer, the worker's check stores it on the `Runner` row, and the step
  collects calls only for such a runner. Single-host and local installs keep the gateway path at
  first, and nothing changes for them. Moving them to the relay later removes
  the gateway from the agent network.
- **Agents configured.** The runner reports whether it has an agent image in
  its environments answer. `runners list` shows it and `runners add` warns
  when it is missing. Placement doesn't use it yet. Until mixed fleets need
  it, every runner that takes subscription runs needs the agent image.

### Security

- No new inbound route to the control plane, no new credential and no
  certificate. Every call goes from the control plane to the runner, over the
  pinned, token-authenticated channel it already uses.
- The lease token never leaves the runner host. A token the agent writes out
  is useless anywhere else.
- Agents reach the relay port only, through the egress proxy. The runner API,
  which starts containers, stays off the agent network.
- The relay bounds request size as `agent_mcp` does (1 MiB), and the number of
  pending calls per session. A call nobody answers times out and the agent
  gets an error.

### Costs and limits

- Each tool call waits for the worker's next collection. Holding the
  collection request open while there are none keeps this under a second.
- Each waiting tool call and each collection holds an open request on the
  runner. Async views keep that from tying up threads.
- Tool audit and rate limits run in the worker instead of the gateway. It is
  the same code.

### Testing

- The relay refuses a missing, wrong or ended lease token, an oversized body
  and calls beyond the pending limit.
- A tool call relayed end to end with a fake agent gets the same answer as
  through `agent_mcp`, including the tool call budget and a refused tool.
- A call left unanswered times out. An answer after the session stops is
  dropped. A retried step does not start the agent again.
- The runner boundary test still passes. The relay imports no control plane
  code.
- The production end-to-end suite runs its deterministic agent on a second
  runner with no route to the gateway.

### Rollout

1. Agents configured: the runner reports it, `runners list` shows it, `runners
   add` warns.
2. The relay, the pending call table, the two endpoints and the worker's
   collection, used only by runners configured for it.
3. A Compose file for a runner on another host, with the relay port and the
   egress proxy's forward, and the deployment guide.
4. The production end-to-end suite with two runners.
