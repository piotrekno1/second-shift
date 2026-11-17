# Runner service

The runner is a small, conventional Django deployment with its own settings,
installed apps, database, credentials, and network policy. Reusing Django gives
it models, generated migrations, transactions, validation, and a typed Ninja
API without giving it access to the control-plane applications.

Only the runner deployment receives `/var/run/docker.sock`. The web, worker,
gateway, and runner use separate settings and database credentials. The runner
does not receive provider keys, OAuth credentials, encrypted connection data, or
the control-plane database URL.

The specs, schemas, paths and limits this API shares with the control plane
live in `secondshift/runner_protocol`. The runner imports no control plane
code.

Its API is deliberately narrow:

- `HEAD /v1/sources/{sha256}` asks whether a source snapshot is already here
- `PUT /v1/sources/{sha256}` uploads a snapshot of one commit's files
- `POST /v1/sessions` creates a sandbox from one uploaded snapshot
- `POST /v1/sessions/{id}/setup/{index}` runs one of the setup commands the
  session spec named, before the agent, with registry-only network access
- `POST /v1/sessions/{id}/commands` executes one idempotent tool call
- `POST /v1/sessions/{id}/collection` stops the sandbox and returns its file
  changes and saved files as one archive
- `DELETE /v1/sessions/{id}` destroys the container and workspace
- `GET /-/health/` is the only unauthenticated endpoint.

All other routes require `SECONDSHIFT_RUNNER_SERVICE_TOKEN`. Session and command
state live in runner-owned Django models and a runner-only database.

The runner keeps no repositories. When a run starts, the control plane uploads a
snapshot of the run's commit, named by its SHA-256. The runner checks the bytes
against that name, unpacks them into the session workspace with size, file
count and entry type limits, and deletes snapshots an hour after their last
use. The runner never accepts a checkout path or clone credential.

Production runners must:

- verify `runsc` is registered and refuse to start otherwise
- unpack only the uploaded snapshot named in the session spec
- accept only server-owned images, mounts and OCI flags
- give command sandboxes no network and no credentials
- run setup commands only before the agent's first call, in a separate
  container that sees only the workspace and reaches only package registries
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
`python:3.13-slim`, see `secondshift/config/constants.py`), or the image of a
named environment a job chooses (`SECONDSHIFT_RUNNER_ENVIRONMENT_<NAME>`). The
runner pulls every one at startup, refuses to start if a pull fails, and runs no
other image.

Runner and control-plane apps share one test suite:

```bash
uv run pytest
```
