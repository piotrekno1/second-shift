# Server configuration

[README](../README.md) · [Deploy](production-deployment.md) · [Environment variables](environment-variables.md) · [Security model](security-model.md)

[Deploy](production-deployment.md) walks through installing SecondShift on a
server, step by step. This page is the reference for what the server operator
can change on the control plane once it runs: outbound access, sign-in,
storage and the limits workspaces may choose from. Sandboxes, images and extra
hosts are in [Runners](runners.md). Every setting is an environment variable.
[Environment variables](environment-variables.md) lists them all.

Settings a workspace owner changes in the dashboard, such as roles, approvals
and each job's limits, are in [Control](control.md). To run
SecondShift on your own machine, see the [Quickstart](/docs/quickstart/).

## Public address and outbound calls

Set `SECONDSHIFT_PUBLIC_URL` to the HTTPS address people use to open the dashboard.
Production refuses to start without it. OAuth sign-in callbacks and run links
use it.

MCP and HTTP API calls leave from the worker, never from a sandbox. In
production they reach only public HTTPS addresses. Local mode also allows local
addresses for testing. Each address is checked again when the connection
opens, so a host cannot pass the check and then resolve to a private address.

## Sign-in and the first workspace

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

Owners invite everyone else from the Settings page, with a role for each
invite. See [roles](access.md#roles).

People can sign in with GitHub when `GITHUB_CLIENT_ID` and
`GITHUB_CLIENT_SECRET` are set, or with an email and password. Set `SECONDSHIFT_EMAIL_URL`
(for example `smtp://user:password@mail.example.com:587`) to send address
confirmations and password resets. Without it SecondShift sends no email, does
not ask people to confirm their address, and cannot reset passwords.

An invite link works once and is shown only when it is created. The server
stores a hash of it, not the link itself. Opening a link never adds anyone by
itself. The person confirms in the dashboard while signed in. Owners can
revoke pending invites from the Settings page.

## Run storage and history

Full run output is stored outside Postgres in private storage, under
`runs/<workspace>/<YYYY>/<MM>/<DD>/<run>/output.log`. Choose the backend with
`SECONDSHIFT_ARTIFACT_STORAGE`:

- `filesystem` (default): files under `SECONDSHIFT_ARTIFACT_ROOT`. Mount a
  persistent volume there in production.
- `s3`: any S3-compatible service. Configure the `SECONDSHIFT_ARTIFACT_*`
  variables in [stored run files](environment-variables.md#stored-run-files). To try it locally, set `COMPOSE_PROFILES=s3` and
  `SECONDSHIFT_ARTIFACT_STORAGE=s3`. Run `process-compose up` to start RustFS and
  let `bootstrap_dev` create the bucket.

Finished runs older than `SECONDSHIFT_RUN_RETENTION_DAYS` (default 90) are
pruned daily by the worker, together with their output and DBOS records.

A job with `history` starts each run with its earlier reports.
`SECONDSHIFT_JOB_HISTORY_MAX` (default 5) is the most a manifest can ask for.
Lowering it also applies to jobs that already ask for more.
`SECONDSHIFT_RUN_HISTORY_MAX_CHARS` (default 65,536) caps their total length.
The newest report is included first. What does not fit is cut or left out.
The files those runs saved are mounted read-only at `/history`, up to 48 MiB.

## Run limits

The server sets the default and the maximum for each run limit and for each
webhook trigger's runs per hour. Owners choose values up to the maximum. The
variables and their defaults are in [server limits](spend.md#server-limits).
The sandbox's memory, CPU, process and per-command limits are in
[Sandbox and compute](sandbox.md#server-settings).

## Testing a server

The [production end-to-end test](../scripts/production_e2e/README.md) checks
gVisor isolation, resource limits, fail-closed startup, upgrades and restores
on a real host. Run it after changing deployment or isolation settings.
