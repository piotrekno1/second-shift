---
name: repository-health
description: Assess this repository's build, tests, migrations, and working tree, then report actionable health issues without modifying the repository.
---

# Repository Health

Read the repository guidance before running checks. Use the documented test,
lint, typecheck, build, and migration-drift commands that apply to changed or
active components.

Report:

- failing checks with the shortest useful error context;
- migration drift or unsafe configuration;
- uncommitted changes that make a scheduled result non-reproducible;
- the specific next action for each problem.

If all relevant checks pass, give a concise healthy summary. Do not modify
files, commit changes, reveal secrets, or trigger external deployments.
