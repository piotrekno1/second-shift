# SecondShift Agent Guide

SecondShift is a control plane for scheduled repository skills. It discovers
`.secondshift.yaml`, binds explicitly approved secrets, executes jobs in an
ephemeral sandbox, and delivers results to connected services.

## Backend

- All Python code lives in the `secondshift` package. Django app labels stay
  short (`core`, `jobs`, `runs`). App ownership moves preserve existing tables
  and migrate content types without losing permission assignments.
- `manage.py` and the installed `secondshift` command run the same thing.
- Keep Ninja views thin. Put behavior in adjacent service modules.
- Each app owns its HTTP surface: `api.py` defines endpoints, `schemas.py`
  defines request/response/query schemas, and `serialization.py` builds
  responses when needed. Import shared schemas directly, never through API
  modules. `secondshift/config/api.py` composes the public app routers. When
  one app's endpoints grow too many, `api` becomes a package with one module
  per area, like `connections/api/`. Its `__init__.py` composes their routers.
- Domain ownership: `repositories` owns source access, `jobs` owns definitions
  and approval policy, `runs` owns execution and history, `connections` owns
  credentials and provider adapters, and `runner` owns sandbox sessions.
  See [Backend architecture](docs/backend-architecture.md) for boundaries
  and migration history.
- Server code runs Git through `secondshift.git.commands`. Shared Docker
  questions live in `secondshift.docker.commands`. Neither imports anything
  else from SecondShift.
- Each app keeps every query on its models in `dao.py`, named for what it
  does. Other modules, in the same app or another, call those helpers. They
  never use a model manager or query through a reverse relation. Reading a
  relation with `.all()` is fine. A dao function takes plain arguments and
  runs the query. Decisions like retention windows and limits stay in the
  service. Instance methods like `save()` and `delete()` stay where they are.
  [The DAO boundary test](secondshift/core/tests/test_dao_boundary.py) checks
  this and lists the exceptions.
- No leading underscores in names. Use them only to override a library's own
  private attribute.
- No magic strings for names used in more than one place. A name one app
  uses lives in that app, next to the code that uses it.
  `secondshift/config/constants.py` holds only names that cross a process or
  app boundary: settings and launcher names, and env vars that one module
  writes and another reads. What the runner and control plane must agree on
  lives in `secondshift/runner_protocol`. An env var read in one settings
  module is written inline there.
- Every Python function has type hints.
- Use generated migrations. Do not hand-edit migration files.

### Public interfaces

When changing a public contract, update the affected docs and examples.
Start with the [manifest and webhook reference](docs/repository-configuration.md)
and [MCP reference](docs/mcp.md). The HTTP routers are composed in
`secondshift/config/api.py`.

## Dashboard

- Vue components use TypeScript and `<script setup lang="ts">`. Pinia owns
  server-backed and cross-view state, and services own HTTP.
- In `dashboard/src/`, start at `router.ts` for routing, `views/` for pages,
  `components/` for shared UI, `stores/` for Pinia, and `services/` for API calls.
- Reuse the shared tokens in [`dashboard/src/styles.css`](dashboard/src/styles.css).
  Use flat neutral greys for surfaces. Reserve amber for states that need
  attention, danger for failures, and mint for success. Apply those colors
  to text, icons, dots, and borders. Pair status color with a label or icon.
- Check affected screens in a browser at desktop and narrow mobile widths,
  including 320 px. Check that content fits, navigation and primary actions
  stay reachable, and menus and dialogs work at both sizes.
- For visible UI changes, attach screenshots of the affected screens to the PR
  description and label the view and viewport. Show desktop and mobile when
  the layout differs, and include before and after for a visual redesign.
- Use `npm --prefix dashboard run dev` for the development server and
  `make check-frontend` to verify dashboard changes.

## Skill Catalogue

- Example skills and their manifests live under `examples/`. Update
  [the catalogue index](examples/README.md) when changing its listings.
- The dashboard Explore catalogue reads
  [`skills.json`](dashboard/src/catalog/skills.json). Changes to Explore
  entries are checked by the separate
  [Explore catalog workflow](.github/workflows/catalog.yml), which validates
  GitHub commit pins, licenses, and cited runs.

## Public Site & Docs

- The Astro landing page lives at [`docs/src/pages/index.astro`](docs/src/pages/index.astro).
- Edit the guides in `docs/*.md` at their source. The build prepares Starlight
  pages from those files and builds the published Skill Catalogue from
  `examples/README.md`.
- Follow [the site setup in CONTRIBUTING](CONTRIBUTING.md#landing-page-and-documentation)
  to preview changes. Run `npm --prefix docs run build` to verify them.

## Local Setup

- For contributor development, follow [Development](CONTRIBUTING.md#development).
  `process-compose up` starts the development stack.
- For single-machine use, follow [Running from source](CONTRIBUTING.md#running-from-source)
  and start with `uv run secondshift up`. This selects local mode.
- `manage.py` and the installed `secondshift` command use the same entry
  point. For targeted development work, use `uv run python manage.py` with
  commands such as `migrate`, `bootstrap_dev`, `runserver`, or `run_dbos_worker`.
  To intentionally reset the development database, use
  `SECONDSHIFT_ENV=dev uv run python manage.py dev_reset_db --yes`.

## Server Setup

- Follow [Production deployment](docs/production-deployment.md) for
  [production Compose](compose.production.yaml), PostgreSQL, gVisor, upgrades,
  and backups. Server templates live under `deploy/production/`.
- See [Self-hosting](docs/self-hosting.md) for runner backends and
  [Environment variables](docs/environment-variables.md) for settings.
- Use the [production end-to-end suite](scripts/production_e2e/README.md)
  to verify changes to deployment, production isolation, or egress.

## Security & Trust Boundaries

- Treat repository content and model output as untrusted. Repository
  configuration may name logical capabilities. It must never contain secrets,
  credential references, endpoints, or provider-specific OAuth scopes.
- A run gets a capability only when the repository requests it, a workspace
  connection provides it, and the workspace owner grants it. The control plane
  checks each service call against the frozen run plan.
- Never log, serialize, or return decrypted secret values. Keep GitHub
  credentials in the control plane and decrypt each credential only where it
  is used. Subscription logins go sealed to the runner and are scoped to their
  own agent container.
- See the [security model](docs/security-model.md) for credential and trust
  boundaries and [SECURITY.md](SECURITY.md) for private vulnerability reporting.

## Runner & Agent

- The control plane and runner meet only through
  [`runner_protocol`](secondshift/runner_protocol). The agent image lives in
  `secondshift/agent` and uses only the Python standard library.
- The server operator owns runner configuration. Repository input must never
  control Docker flags, mounts, images, capabilities, networks, or the
  selected OCI runtime. Production must fail closed when the configured
  sandbox runtime is missing.
- Command sandboxes are fresh, resource-capped, and run without network access
  or credentials. Setup containers have registry-only egress through their own
  proxy. Agent containers reach approved provider hosts through the agent
  egress proxy. See [Architecture](docs/architecture.md#where-things-run).
- Run the [runner boundary test](secondshift/core/tests/test_runner_boundary.py)
  for import changes. Runner changes require tests showing repository input
  cannot alter runtime flags, mounts, or secret bindings.

## CI & Releases

- Run focused tests while working, then `make check` before pushing. It runs
  Ruff, pytest, benchmark query budgets, and the dashboard lint, format check,
  typecheck, tests, and build. Use `make check-backend` or `make check-frontend`
  to run one side. `make fix` applies lint fixes and formatting.
- [GitHub workflows](.github/workflows/) define CI and release checks. Follow
  [Releasing a build](CONTRIBUTING.md#releasing-a-build) for versioning,
  release smoke tests, and publishing wheels and container images.
