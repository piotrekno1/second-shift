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

Set `FRONTEND_URL` to the HTTPS address people use to open the dashboard.
Production refuses to start without it. OAuth sign-in callbacks and run links
use it.

MCP and HTTP API calls leave from the worker, never from a sandbox. In
production they reach only public HTTPS addresses. Local mode also allows local
addresses for testing. Each address is checked again when the connection
opens, so a host cannot pass the check and then resolve to a private address.

## Run artifact storage

Full run output is stored outside Postgres in private storage, under
`runs/<workspace>/<YYYY>/<MM>/<DD>/<run>/output.log`. Choose the backend with
`SECONDSHIFT_ARTIFACT_STORAGE`:

- `filesystem` (default): files under `SECONDSHIFT_ARTIFACT_ROOT`. Mount a
  persistent volume there in production.
- `s3`: any S3-compatible service. Configure the `SECONDSHIFT_ARTIFACT_*`
  variables in `.env.example`. To try it locally, set `COMPOSE_PROFILES=s3` and
  `SECONDSHIFT_ARTIFACT_STORAGE=s3`. Run `process-compose up` to start RustFS and
  let `bootstrap_dev` create the bucket.

Finished runs older than `SECONDSHIFT_RUN_RETENTION_DAYS` (default 90) are
pruned daily by the worker, together with their output and DBOS records.

