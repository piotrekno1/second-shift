# Runner service

The runner is a small, conventional Django deployment with its own settings,
installed apps, database, credentials, and network policy. Reusing Django gives
it models, generated migrations, transactions, validation, and a typed Ninja
API without giving it access to the control-plane applications.

Only the runner deployment receives `/var/run/docker.sock`. The web, worker,
gateway, and runner use separate settings and database credentials. The runner
does not receive provider keys, OAuth credentials, encrypted connection data, or
the control-plane database URL.

Its API is deliberately narrow:

- `POST /v1/sessions` creates a sandbox for one immutable repository commit
- `POST /v1/sessions/{id}/commands` executes one idempotent tool call
- `DELETE /v1/sessions/{id}` destroys the container and workspace
- `GET /-/health/` is the only unauthenticated endpoint.

All other routes require `SECONDSHIFT_RUNNER_SERVICE_TOKEN`. Session and command
state live in runner-owned Django models and a runner-only database.

The runner reads repositories only from `SECONDSHIFT_RUNNER_REPOSITORY_ROOT`.
In development, the worker maintains bare Git mirrors there under a per-source
lock. This bridge is enabled only for locally attached repositories under dev
settings. Hosted deployments should populate the same immutable source store
through their GitHub ingestion service. The runner never accepts a checkout
path or clone credential in its session API.

Production runners must:

- verify `runsc` is registered and refuse to start otherwise
- export the repository at the recorded full commit SHA
- accept only server-owned images, mounts and OCI flags
- give command sandboxes no network and no credentials
- use a dedicated sandbox per run with bounded resources
- keep sandbox images free of the control-plane application and Docker client
- deduplicate tool calls by their stable run and call identifiers
- send logs and the final result back through an authenticated channel
- destroy the workspace after the result is acknowledged.

Start the service only after applying its own migrations:

```bash
DJANGO_SETTINGS_MODULE=secondshift.config.settings.runner uv run python manage.py migrate
SECONDSHIFT_SERVICE=runner uv run gunicorn --config python:secondshift.config.gunicorn
```

Required configuration is in `secondshift/config/settings/runner.py`. Production should use
`runsc`. Standard Docker is reserved for trusted development and single-tenant
self-hosting. Service startup verifies the selected runtime and fails closed.
The runner uses the same image as the control plane, with its own command,
root user override, database and network identity.
Sandboxes use `SECONDSHIFT_RUNNER_IMAGE` (default: the pinned official
`python:3.13-slim`, see `secondshift/config/constants.py`). The runner pulls it at startup and
refuses to start if the pull fails.

Runner and control-plane apps share one test suite:

```bash
uv run pytest
```
