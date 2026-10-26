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

Squash migrations with `squashmigrations` and keep the files they replace, so
existing databases can still upgrade. Delete the replaced files only when no
install needs to upgrade from a release that still used them. The squashes
added in 0.1.0a6 replace migrations from 0.1.0a5 and older. Leave
`workspaces/0001_initial` and `core/0004_move_workspaces_to_their_app` out of
any squash. They move the workspace models between apps, and a squash that
includes them breaks a fresh PostgreSQL install. Test a new squash on
PostgreSQL, because SQLite does not catch this.

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
