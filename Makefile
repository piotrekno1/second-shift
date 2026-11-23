# The checks CI runs, in one place. Run `make check` before pushing.
# Install dependencies first with `uv sync` and `npm --prefix dashboard ci`.

BENCHMARKS = uv run pytest secondshift/benchmarks -o python_files='bench_*.py'

.PHONY: check check-backend check-frontend check-api-types fix

check: check-backend check-frontend check-api-types

check-backend:
	uv run ruff check --no-cache .
	uv run ruff format --check --no-cache .
	uv run pytest
	$(BENCHMARKS) --benchmark-disable

check-frontend:
	npm --prefix dashboard run lint
	npm --prefix dashboard run format:check
	npm --prefix dashboard run typecheck
	npm --prefix dashboard run test
	npm --prefix dashboard run build

check-api-types:
	npm --prefix dashboard run generate:api-types -- --check

# Rewrites files. Fixes what the linters can and formats everything.
fix:
	uv run ruff check --fix .
	uv run ruff format .
	npm --prefix dashboard run lint -- --fix
	npm --prefix dashboard run format
