# SecondShift Agent Guide

SecondShift is a control plane for scheduled repository skills. It discovers
`.secondshift.yaml`, binds explicitly approved secrets, executes jobs in an
ephemeral sandbox, and delivers results to connected services.

## Commands

```bash
uv sync
uv run python manage.py migrate
uv run python manage.py bootstrap_dev
SECONDSHIFT_ENV=dev uv run python manage.py dev_reset_db --yes
uv run python manage.py runserver
uv run secondshift up   # the installed single-machine mode (SECONDSHIFT_ENV=local)
uv run python manage.py run_dbos_worker
uv run pytest
uv run ruff check .
uv run ruff format .

cd dashboard && npm install
cd dashboard && npm run dev
cd dashboard && npm run typecheck
cd dashboard && npm run test
cd dashboard && npm run build

process-compose up
docker build .
```

## Conventions

- All Python code lives in the `secondshift` package. Django app labels stay
  short (`core`, `jobs`, `runs`). App ownership moves preserve existing tables
  and migrate content types without losing permission assignments.
- `manage.py` and the installed `secondshift` command run the same thing.
- Keep Ninja views thin. Put behavior in adjacent service modules.
- Each app owns its HTTP surface: `api.py` defines endpoints, `schemas.py`
  defines request/response/query schemas, and `serialization.py` builds
  responses when needed. Import shared schemas directly, never through API
  modules. `secondshift/config/api.py` composes the public app routers.
- Domain ownership: `repositories` owns source access, `jobs` owns definitions
  and approval policy, `runs` owns execution and history, `connections` owns
  credentials and provider adapters, and `runner` owns sandbox sessions.
  See `docs/backend-architecture.md` for boundaries and migration history.
- No leading underscores in names. Use them only to override a library's own
  private attribute.
- No magic strings for names used in more than one place. Shared names
  (environments, services, sandbox runtimes, the default image) live in
  `secondshift/config/constants.py` as `StrEnum`s or constants.
- Every Python function has type hints.
- Never log, serialize, or return decrypted secret values.
- Repository configuration may name logical capabilities, but may never contain
  secrets, credential references, endpoints, or provider-specific OAuth scopes.
- Runner configuration is server-owned. Repository input must never control
  Docker flags, mounts, images, capabilities, or the selected OCI runtime.
- Production must fail closed when the configured sandbox runtime is missing.
- Vue components use TypeScript and `<script setup lang="ts">`. Pinia owns
  server-backed and cross-view state; services own HTTP.
- Use generated migrations. Do not hand-edit migration files.

## Verification

- Backend: focused pytest, then the full pytest suite and `ruff check`.
- Frontend: `npm run typecheck`, `npm run test`, and `npm run build` when routes
  or bootstrapping change.
- Runner changes require tests showing repository input cannot alter runtime
  flags, mounts, or secret bindings.
