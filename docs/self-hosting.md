# Self-hosting

[README](../README.md) · [Architecture](architecture.md) · [Security model](security-model.md)

> **Work in progress.** A production deployment is not yet documented or
> verified end to end. Follow
> [issue #1](https://github.com/piotrekno1/second-shift/issues/1) for progress.
> Local mode (`secondshift up`) is the supported way to try SecondShift today.

## Runner backends

Self-host SecondShift to run committed skills against trusted repositories
with your own model account and deliver reports to team channels. You can move
to a deployment with stronger isolation without changing the skills.

- `fake`: records requests without executing repository code. For tests only.
- `local-docker`: hardened Docker for development and trusted, single-tenant
  self-hosting.
- `isolated-runner`: a separate Linux runner using `runsc` for hosted or
  untrusted workloads.

There is no normal no-sandbox backend. Backends are deployment choices with the
same functional runner contract, not job features. Production settings fail
closed unless the configured isolation policy is available. SecondShift never
accepts agent drivers, runtime flags, images, capabilities, networks or mounts
from a repository manifest.

### Sandbox image

Skill commands run in Docker's official `python:3.13-slim` image, pinned by
digest in `secondshift/config/constants.py`. The runner pulls it when it starts, so there is
nothing to build or host.

Operators can set `SECONDSHIFT_RUNNER_IMAGE` to their own image, for example
one built `FROM python:3.13-slim` with Node or Go added for skills that need
them. Repositories can never choose the image. A custom image must:

- provide `sleep`, `find`, `cat` and `grep` (any Debian or Alpine base does)
- work as an unprivileged user with a read-only root filesystem, no network,
  `HOME=/tmp` and the repository mounted at `/workspace`
- contain no credentials, Docker client or SecondShift code.

Git is not needed in the image: the runner checks out the commit itself. Pin
custom images by digest (`name@sha256:…`) so a pushed tag can't change what runs.

Self-hosting the control plane does not by itself keep repository content local:
the configured model provider receives the context sent to it. An
OpenAI-compatible local provider can be used where the entire execution path
must remain within the operator's environment.

## Public address and outbound calls

Set `SECONDSHIFT_PUBLIC_URL` to the HTTPS address people use to open the dashboard.
Production refuses to start without it. OAuth sign-in callbacks and run links
use it.

MCP and HTTP API calls leave from the worker, never from a sandbox. In
production they reach only public HTTPS addresses. Local mode also allows local
addresses for testing. Each address is checked again when the connection
opens, so a host cannot pass the check and then resolve to a private address.

## First owner and invites

Sign-up is closed in production unless `SECONDSHIFT_SIGNUP_ENABLED` is set.
People join through invite links instead.

Create the first workspace on the server:

```bash
secondshift create_workspace "Acme"
```

It prints a one-time link. Whoever opens it can sign in or sign up, even with
sign-up closed, and becomes the workspace's owner after confirming. The link
works for seven days. If it expires unused, run the command again for a new
one. Once the workspace has an owner, the command refuses to make another.

Staff accounts can also create workspaces from the dashboard, with the
workspace menu under the logo. They own each workspace they create. The account
`secondshift up` makes is staff. With sign-up open, anyone can create
workspaces this way, up to three each. Staff have no limit. With sign-up
closed, people who are not staff can only join through invites.

People in more than one workspace switch between them with the same menu.

Owners invite everyone else from the Settings page. Each invite has a role:

- **Owner** connects services, approves access, invites people and manages
  their roles.
- **Admin** enables, runs and schedules approved jobs, manages repositories
  and changes run limits.
- **Member** can view.

People can sign in with GitHub when `GITHUB_CLIENT_ID` and
`GITHUB_CLIENT_SECRET` are set, or with an email and password. Set `SECONDSHIFT_EMAIL_URL`
(for example `smtp://user:password@mail.example.com:587`) to send address
confirmations and password resets. Without it SecondShift sends no email, does
not ask people to confirm their address, and cannot reset passwords.

An invite link works once and is shown only when it is created. The server
stores a hash of it, not the link itself. Opening a link never adds anyone by
itself. The person confirms in the dashboard while signed in. Owners can
revoke pending invites from the Settings page.

Owners also change roles and remove people on the Settings page. A workspace
always keeps at least one owner, so the last owner cannot step down or leave.

## Run artifact storage

Full run output is stored outside Postgres in private storage, under
`runs/<workspace>/<YYYY>/<MM>/<DD>/<run>/output.log`. Choose the backend with
`SECONDSHIFT_ARTIFACT_STORAGE`:

- `filesystem` (default): files under `SECONDSHIFT_ARTIFACT_ROOT`. Mount a
  persistent volume there in production.
- `s3`: any S3-compatible service. Configure the `SECONDSHIFT_ARTIFACT_*`
  variables in the [environment reference](environment-variables.md). To try it locally, set `COMPOSE_PROFILES=s3` and
  `SECONDSHIFT_ARTIFACT_STORAGE=s3`. Run `process-compose up` to start RustFS and
  let `bootstrap_dev` create the bucket.

Finished runs older than `SECONDSHIFT_RUN_RETENTION_DAYS` (default 90) are
pruned daily by the worker, together with their output and DBOS records.

Each run has four limits. Each has a default and a maximum:

- `SECONDSHIFT_RUN_TIMEOUT_SECONDS` (default 900, so 15 minutes) limits time.
  The clock starts when the run starts, so time in the queue does not count.
- `SECONDSHIFT_AGENT_MAX_TURNS` (default 24) counts model requests, not tool
  actions. One request can ask for several tool calls.
- `SECONDSHIFT_AGENT_MAX_TOOL_CALLS` (default 64) counts file reads, searches
  and commands.
- `SECONDSHIFT_MODEL_RUN_TOKEN_BUDGET` (default 2,000,000) counts input and
  output tokens. The model gateway enforces it.

The workspace owner can change these limits on the Settings page for the whole
workspace, and on a job's page for that job. A job's limit wins over the
workspace's, and the workspace's wins over the default. No value can go above
its maximum. Set the maximum with the same variable plus `_CEILING`, for
example `SECONDSHIFT_AGENT_MAX_TURNS_CEILING`. Each maximum defaults to its
limit, so owners can only lower a limit until you raise the maximum. Lowering a
maximum later also caps values owners already chose. `.secondshift.yaml` cannot
set limits.

A run's limits are fixed when it is created and stored in its execution plan.
Changing a limit affects the next run, not one that is queued or running.

The run checks the time limit before each model request and each tool call,
and its model access ends at the limit. A run that passes it fails with the
reason **Timed out** and keeps what the agent wrote so far as a partial report.
Each sandbox command also has its own limit,
`SECONDSHIFT_RUNNER_COMMAND_TIMEOUT_SECONDS` (default 120).

When the agent reaches its last model request, or has no tool calls left, its
tools are taken away and it is asked to write the report with what it has. The
run then succeeds and its timeline notes that the budget ran out. A run that
still goes past a limit, or hits the token budget, fails with the reason
**Budget used up**. It names the limit and keeps what the agent wrote so far as
a partial report. The run page links to the job's limits, and runs show their
token use against the budget, for example `12.3k / 2m tok`.
