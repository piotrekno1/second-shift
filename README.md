# SecondShift

**GitOps for recurring agent work.**

SecondShift runs versioned agent skills on a schedule. Commit a skill beside
your code, connect a model, approve the services it may use, and get an audited
result tied to an exact repository revision.

A root-level `.secondshift.yaml` says **what** to run and **when**. The server
controls **how** it runs, including credentials, models, sandboxes and network
access.

```yaml
version: 1
jobs:
  infrastructure-review:
    name: Infrastructure review
    skill: .agents/skills/infrastructure-review/SKILL.md
    schedule: "0 9 * * 1"
    timezone: Europe/Warsaw
    requires:
      mcps:
        - binding: observability
          tools:
            - search_dashboards
            - query_metrics
          required: true
    notify:
      - binding: engineering-alerts
```

Here, `observability` is a logical name. The workspace maps it to a real
connection and approves the requested tools. Expanding the request requires a
new approval.

## Run it on your computer

You need Python 3.13 and Docker. Skills run in Docker sandboxes.

```bash
uvx secondshift up
```

SecondShift is not on PyPI yet. Until it is, run it from a checkout:

```bash
npm --prefix dashboard install && npm --prefix dashboard run build
uv run secondshift up
```

It opens <http://127.0.0.1:8000> and signs you in with a one-time link. There
is no password to set. The link is also printed in the terminal. It works once
and expires after 15 minutes. Run `secondshift login` for a new one.
Everything is kept in `~/.secondshift`. Press Ctrl+C to stop. Add `--port` to
use another port.

To reach it from other machines, add `--host 0.0.0.0`. The first time, it asks
for an owner email and password, because the network can now reach it.

This mode is for one person on their own machine. It uses plain Docker and can
read Git repositories from your disk. For a shared server, see "Runner
backends and self-hosting" below.

## Product walkthrough

### 1. Sign in and hand over the shift

GitHub provides identity, while repository installations and service
connections remain separately scoped and revocable.

![SecondShift sign-in screen](docs/screenshots/01-sign-in.png)

### 2. See what is on duty

The control room summarizes enabled repositories and schedules, today's runs,
failures that need attention, recent activity, and the next scheduled shift.

![SecondShift control room](docs/screenshots/02-overview.png)

### 3. Review Git-backed schedules

Jobs are discovered from `.secondshift.yaml`. Owners can review their skill,
schedule, timezone, next execution, and enabled state before running them.

![SecondShift jobs and schedules](docs/screenshots/03-jobs.png)

### 4. Connect services and approve capabilities

GitHub, ChatGPT, and Slack are authorized independently. Logical bindings map
repository intent to workspace-owned MCPs, APIs, and notification targets.

![SecondShift connections and encrypted secrets](docs/screenshots/04-connections.png)

### 5. Follow the durable audit trail

Every scheduled or manual execution is tied to a repository commit and records
its state and human-readable outcome.

![SecondShift run audit trail](docs/screenshots/05-runs.png)

## Development

Requirements: Python 3.13, `uv`, Node.js 24+, Docker, and process-compose.

```bash
cp .env.example .env
uv sync
cd dashboard && npm install && cd ..
process-compose up
```

Open the dashboard at <http://localhost:5173> and Django admin at
<http://localhost:5173/-/admin/>. The development bootstrap creates
`admin@secondshift.local` with password `secondshift`; override both through
`DEV_ADMIN_EMAIL` and `DEV_ADMIN_PASSWORD`. A normal startup creates only this
admin and its empty workspace—never sample repositories, jobs or runs.

To replace the development database with credential-free sample data for UI
exploration, stop SecondShift processes, leave PostgreSQL running, and run:

```bash
DJANGO_ENV=dev uv run python manage.py dev_reset_db --yes
```

This deliberately drops and recreates the configured development database,
applies migrations, runs the base bootstrap, and then adds disabled demo jobs
and representative completed runs. Never use it for normal startup.

The development stack also starts the separate runner API on port 8002. Before
a local-repository run, the worker copies the frozen Git commit into
`~/.secondshift/runner/repositories`; the runner never receives the
repository's original host path.

### Data folder

Without explicit settings, SecondShift keeps its state in `~/.secondshift`
(set `SECONDSHIFT_DATA_DIR` to move it):

| Path | Contents |
|---|---|
| `.env` | Settings, read after a checkout's `.env`. In development, the first start writes fresh keys here. Back it up: `FIELD_ENCRYPTION_KEY` decrypts stored connection credentials |
| `runner.env` | The runner's token. The runner reads only this file |
| `secondshift.sqlite3` | The database, unless `DATABASE_URL` is set |
| `artifacts/`, `work/` | Run input and output files, and scratch space |
| `secondshift/runner/` | The runner's database, sandbox workspaces and Git mirrors |

The real environment wins over a checkout's `.env`, which wins over the data
folder's. Only development generates keys (`secondshift/config/first_run.py`); production
refuses to start until
`DJANGO_SECRET_KEY`, `FIELD_ENCRYPTION_KEY` and
`SECONDSHIFT_RUNNER_SERVICE_TOKEN` are set.

In development, the Repositories page can attach a local Git repository by
path. SecondShift resolves its Git root, requires a committed root-level
`.secondshift.yaml` and committed referenced `SKILL.md` files, records the HEAD
revision, and synchronizes its jobs. The feature is enabled only by dev settings
and can be hidden with `SECONDSHIFT_LOCAL_REPOSITORIES_ENABLED=false`.

Repository cards use the human-readable directory name and link to a detail
view with the owner-visible local path, branch, synchronized and current
revisions, manifest health, discovered jobs, and grant state. Local owners can
validate, resync, enable or disable, and remove a source. Removal is soft: it
disables its jobs and hides the source while preserving historical runs.

DBOS stores durable workflow, queue, and schedule state in PostgreSQL. The
dedicated worker reconciles enabled Django jobs into DBOS schedules and resumes
interrupted runs from their last completed step. Workflow arguments contain
only database identifiers; decrypted secrets never enter DBOS state.

## Execution design

SecondShift keeps four choices separate:

| Concern | Example | Controlled by |
| --- | --- | --- |
| Skill | `repository-health` | Repository author |
| Agent | SecondShift Agent | Workspace policy |
| Model | Claude, GPT or a local model | Workspace owner |
| Sandbox | Docker or `runsc` | Server operator |

The manifest selects a skill and requests capabilities. It cannot choose an
agent executable, credential, image, mount or network. The server records all
effective choices in an immutable execution plan. The first agent is
provider-neutral; native Codex or Claude Code adapters can be added later by the
server operator.

## Runtime architecture

```text
DBOS scheduler -> Agent orchestrator -> Model gateway -> Model provider
                         |
                         +-> Capability broker -> Approved services
                         |
                         +-> Runner service -> Networkless run sandbox
```

- The orchestrator runs the agent loop but never receives a Docker socket or
  provider key.
- The runner alone owns the container runtime. Each run gets an ephemeral,
  resource-limited sandbox with no network or credentials.
- The gateway enforces model access, budgets and usage metering.
- The broker executes only approved external tools.
- DBOS checkpoints each turn and deduplicates retried calls. Large output goes
  to private artifact storage.

## Capability and policy boundary

A run receives only tools that are requested by the repository, available on a
workspace connection, and explicitly granted. Missing or incomplete required
access stops the run before dispatch.

Repository content and model output are untrusted. Commands stay inside the
networkless sandbox; MCP and API calls go through the broker and are checked
against the frozen execution plan. External writes require a separate grant.

Credentials remain in the gateway or broker. They never appear in repository
configuration, workflow arguments, execution plans or command sandboxes. Shell
allowlists are not considered a security boundary—the sandbox and broker are.

## Run artifact storage

Full run output is stored outside Postgres in private storage, under
`runs/<workspace>/<YYYY>/<MM>/<DD>/<run>/output.log`. Choose the backend with
`SECONDSHIFT_ARTIFACT_STORAGE`:

- `filesystem` (default): files under `SECONDSHIFT_ARTIFACT_ROOT`. Mount a
  persistent volume there in production.
- `s3`: any S3-compatible service. Configure the `SECONDSHIFT_ARTIFACT_*`
  variables in `.env.example`. To try it locally, set `COMPOSE_PROFILES=s3` and
  `SECONDSHIFT_ARTIFACT_STORAGE=s3`; `process-compose up` then starts RustFS and
  `bootstrap_dev` creates the bucket.

Finished runs older than `SECONDSHIFT_RUN_RETENTION_DAYS` (default 90) are
pruned daily by the worker, together with their output and DBOS records.

## Runner backends and self-hosting

SecondShift is intended to be useful as a self-hosted control plane, not merely
as a hosted sandbox. A small installation can connect trusted repositories and
a bring-your-own model account, run committed skills on a schedule, and deliver
their reports to existing team channels. The same repository contract can move
to a more strongly isolated deployment without changing the skills.

- `fake`: tests only; records requests without executing repository code.
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

- provide `sleep`, `find`, `cat` and `grep` (any Debian or Alpine base does);
- work as an unprivileged user with a read-only root filesystem, no network,
  `HOME=/tmp` and the repository mounted at `/workspace`;
- contain no credentials, Docker client or SecondShift code.

Git is not needed in the image: the runner checks out the commit itself. Pin
custom images by digest (`name@sha256:…`) so a pushed tag can't change what runs.

Self-hosting the control plane does not by itself keep repository content local:
the configured model provider receives the context sent to it. An
OpenAI-compatible local provider can be used where the entire execution path
must remain within the operator's environment.

### Implementation status

The frozen tool policy, Pydantic AI model adapters, runner-backed workspace
tools, model gateway, and authenticated Django runner service are in place.
The durable workflow now creates an offline sandbox, runs the selected skill,
stores its report, and destroys the sandbox. Runs requesting MCP or API tools
still fail closed until the capability broker is connected. Local development
materializes immutable commits into the runner store; hosted GitHub ingestion
is still pending. See
`secondshift/automations/README.md` for the code map and `secondshift/runner/README.md` for the
deployment boundary that must be preserved.

## License

GNU Affero General Public License v3.0. Commercial licensing can be offered
separately by the copyright holder.
