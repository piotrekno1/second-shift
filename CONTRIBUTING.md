# Contributing to SecondShift

## Running from source

For the single-person local mode, install Python 3.13, uv, Node.js 24+, and
Docker, then run:

```bash
git clone https://github.com/piotrekno1/second-shift.git
cd second-shift
npm --prefix dashboard ci
npm --prefix dashboard run build
uv run secondshift up
```

For the development stack with live frontend updates, follow the setup below.

## Development

Requirements: Python 3.13, `uv`, Node.js 24+, Docker, and process-compose.

```bash
cp .env.example .env
uv sync
cd dashboard && npm install && cd ..
process-compose up
```

Process-compose loads `.env` and selects development mode for its processes. Initialization
creates the control-plane keys and runner token before migrating the runner.
Gunicorn automatically loads the root `gunicorn.conf.py`, which delegates to
the shared service configuration in `secondshift/config/gunicorn.py`.
The supporting PostgreSQL and optional RustFS services are defined in
`docker-compose.localdev.yml`; standalone Docker commands need
`docker compose -f docker-compose.localdev.yml`.

The template contains the development overrides. See the
[environment reference](docs/environment-variables.md) for optional integrations,
email, logging, and sandbox settings. In dev and local mode, keys are generated in `SECONDSHIFT_DATA_DIR` (default `~/.secondshift`),
email defaults to terminal output, and plain Docker sandboxes are supported.

To use the optional local RustFS service, uncomment its block in `.env`
(copied from `.env.example`):

```dotenv
COMPOSE_PROFILES=s3
SECONDSHIFT_ARTIFACT_STORAGE=s3
SECONDSHIFT_ARTIFACT_BUCKET=secondshift-artifacts
SECONDSHIFT_ARTIFACT_ENDPOINT_URL=http://localhost:9000
SECONDSHIFT_ARTIFACT_REGION=us-east-1
SECONDSHIFT_ARTIFACT_ACCESS_KEY_ID=secondshift
SECONDSHIFT_ARTIFACT_SECRET_ACCESS_KEY=secondshift-dev-secret
SECONDSHIFT_ARTIFACT_ADDRESSING_STYLE=path
```

Restart process-compose after changing the profile. Bootstrap creates the local
bucket. The RustFS console is at `http://localhost:9001/rustfs/console/`; sign in
with the development artifact access key and secret above.

### Releasing a build

Releases are created by pushing a version tag on a commit that belongs to
`main`. Use Python version notation: `v0.1.0a1` for an alpha, `v0.1.0b1` for a
beta, `v0.1.0rc1` for a release candidate, or `v0.1.0` for a stable release.

1. Update `project.version` in `pyproject.toml`, run `uv lock`, and merge both
   files into `main` with the release changes.
2. Run the **Release smoke test** workflow on `main` from the Actions tab. It
   is the same test the release runs, so a problem shows up before you tag.
3. Tag that commit and push the tag, for example:

   ```bash
   git switch main
   git pull --ff-only
   git tag -a v0.1.0a1 -m "SecondShift 0.1.0a1"
   git push origin v0.1.0a1
   ```

The Release workflow rejects tags outside `main` or versions that do not match
`pyproject.toml`. It runs backend and frontend checks, builds the dashboard and
wheel, verifies bundled assets, and runs the release smoke test. It then
publishes the wheel and installation instructions to a GitHub release. Alpha, beta and release candidate tags become prereleases.
The workflow uses GitHub's built-in token. It does not publish to PyPI.
Use a new version and tag for each build. Do not move published tags.

After publishing the wheel release, the release workflow calls **Publish container
image**. It builds the exact tagged commit on native amd64 and arm64 runners,
checks the installed package and bundled dashboard, and publishes
`ghcr.io/piotrekno1/second-shift:VERSION`. Stable versions also update `latest`;
prereleases do not. Published version manifests must not be replaced.

To add an image for an existing release, run **Publish container image** manually
on `main` with its release tag (for example `v0.1.0b1`). It verifies that the tag
belongs to `main`, matches the package version, and has a published GitHub release.
The workflow uses `GITHUB_TOKEN` with `packages: write`; no registry secret is
needed. After first publication, check the package's visibility in GitHub Packages
and set it to Public so deployments can pull anonymously.


The release smoke test installs the wheel into a clean environment outside the
checkout and starts `secondshift up` with its own data folder. A fake model
and a webhook receiver stand in for real services. It runs one skill that must
read a file in the sandbox and deliver its report, and one that the fake model
rejects, which must fail with a readable reason and still notify. On failure
it keeps the logs in `smoke-diagnostics`. To run it locally, Docker must be
running:

```bash
npm --prefix dashboard run build
uv build --wheel
uv run --no-project python scripts/release_smoke/run.py dist/*.whl
```

### Development environment

Open the dashboard at <http://localhost:5173> and Django admin at
<http://localhost:5173/-/admin/>. The development bootstrap creates
`admin@secondshift.local` with password `secondshift`. Override both through
`DEV_ADMIN_EMAIL` and `DEV_ADMIN_PASSWORD`. A normal startup creates only this
admin and an empty workspace. It creates no sample repositories, jobs or runs.

To replace the development database with credential-free sample data for UI
exploration, stop SecondShift processes, leave PostgreSQL running, and run:

```bash
SECONDSHIFT_ENV=dev uv run python manage.py dev_reset_db --yes
```

This deliberately drops and recreates the configured development database,
applies migrations, runs the base bootstrap, and then adds disabled demo jobs
and representative completed runs. Never use it for normal startup.

The development stack also starts the separate runner API on port 8002. Before
a local-repository run, the worker copies the frozen Git commit into
`~/.secondshift/runner/repositories`. The runner never receives the
repository's original host path.

### Data folder

Without explicit settings, SecondShift keeps its state in `~/.secondshift`
(set `SECONDSHIFT_DATA_DIR` to move it):

| Path | Contents |
|---|---|
| `.env` | Settings, read after a checkout's `.env`. In development, the first start writes fresh keys here. Back it up: `SECONDSHIFT_ENCRYPTION_KEY` decrypts stored connection credentials |
| `runner.env` | The runner's token. The runner reads only this file |
| `secondshift.sqlite3` | The database, unless `SECONDSHIFT_DATABASE_URL` is set |
| `artifacts/`, `work/` | Run input and output files, and scratch space |
| `secondshift/runner/` | The runner's database, sandbox workspaces and Git mirrors |

The real environment wins over a checkout's `.env`, which wins over the data
folder's. Development generates keys through `secondshift/config/first_run.py`.
Production refuses to start until
`SECONDSHIFT_SECRET_KEY`, `SECONDSHIFT_ENCRYPTION_KEY` and
`SECONDSHIFT_RUNNER_SERVICE_TOKEN` are set, and `SECONDSHIFT_PUBLIC_URL` is an HTTPS
address.

In development, the Repositories page can attach a local Git repository by
path. SecondShift resolves its Git root and follows the checked-out branch. It
reads the root-level `.secondshift.yaml` and referenced `SKILL.md` files from
that branch's newest commit, records the revision, and synchronizes its jobs.
The worker syncs again when the branch moves
(`SECONDSHIFT_REPOSITORY_SYNC_INTERVAL_SECONDS`, 0 turns it off). The feature is enabled only by dev settings
and can be hidden with `SECONDSHIFT_LOCAL_REPOSITORIES_ENABLED=false`.

Repository cards use the human-readable directory name and link to a detail
view with the owner-visible local path, branch, synchronized and current
revisions, manifest health, discovered jobs, and grant state. Local owners can
validate, resync, enable or disable, and remove a source. Removal is soft: it
disables its jobs and hides the source while preserving historical runs.

DBOS stores durable workflow, queue, and schedule state in PostgreSQL. The
dedicated worker reconciles enabled Django jobs into DBOS schedules and resumes
interrupted runs from their last completed step. Workflow arguments contain
only database identifiers. Decrypted secrets never enter DBOS state.

## Verification

Run backend checks from the repository root:

```bash
uv run pytest
uv run ruff check .
```

Run frontend checks:

```bash
npm --prefix dashboard run typecheck
npm --prefix dashboard run test
npm --prefix dashboard run build
```

Use focused tests while making changes, then run the full checks before
submitting. Generate Django migrations with `makemigrations`. Do not hand-edit
them.

First-party migrations have been reset to the current app structure. Databases
from before this reset cannot upgrade in place. Back them up and start with
fresh control-plane and runner databases; do not use `--fake-initial` to reuse
the old schema. For development, `dev_reset_db --yes` recreates the configured
development database. Reset a separately configured runner database as well.

Keep new schema changes incremental with generated migrations. Test fresh
migrations on PostgreSQL as well as SQLite, since SQLite can miss dependency
ordering problems. See [database migrations](docs/backend-architecture.md#database-migrations)
for the reset's compatibility boundary.

### Benchmarks

`secondshift/benchmarks` times the paths that grow with use. These are
manifest parsing, repository sync, stream metering in the model gateway, the
runs and overview endpoints, MCP tool calls and gateway token lookups. The
normal test run skips them.

```bash
# Time everything and print a table.
uv run pytest secondshift/benchmarks -o python_files='bench_*.py'

# Save a baseline, then compare a branch against it.
uv run pytest secondshift/benchmarks -o python_files='bench_*.py' --benchmark-autosave
uv run pytest secondshift/benchmarks -o python_files='bench_*.py' --benchmark-compare
```

Each database benchmark also has a query budget. Query counts do not depend
on the machine, so CI runs every benchmark once with `--benchmark-disable` and
fails when a change adds queries. If a change lowers a count, lower its
budget too. Timings come from the in-memory SQLite test database. Treat them
as relative numbers, not production latency.

## Installed builds and troubleshooting

If uv reports that its tool directory is missing from your `PATH`, run
`uv tool update-shell` and restart your terminal.

To upgrade an installed build, download the new release's wheel and run the
following with its filename:

```bash
uv tool install --force --python 3.13 ./secondshift-VERSION-py3-none-any.whl
```

The release notes include the exact filename and installation commands.

Alpha releases can replace their database migrations, so an upgrade between
alphas may not start on an existing `~/.secondshift`. Back up that folder
before upgrading. If the new version reports a migration error, stop it, move
`~/.secondshift` aside, and run `secondshift up` again to start fresh.
Connections and approvals then need to be set up again.

Local mode opens <http://127.0.0.1:8000> and signs you in with a one-time link.
The link is also printed in the terminal, works once, and expires after 15
minutes. Run `secondshift login` for a new one. Everything is kept in
`~/.secondshift`. Add `--port` to `secondshift up` to use another port.

To reach it from other machines, add `--host 0.0.0.0`. The first time, it asks
for an owner email and password because the network can now reach it.

Local mode is for one person on their own machine. It uses plain Docker and
can read Git repositories from disk. For a shared server, see
[self-hosting guide](docs/self-hosting.md).
