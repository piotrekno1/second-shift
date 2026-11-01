# Task examples

These examples demonstrate reviews, briefings, and ticket updates through
approved MCP tools or API operations.
See the [task setup guide](../docs/engineering-tasks.md#tasks-that-change-service-data)
for an action workflow and its access requirements.

Each directory contains the canonical `.agents/skills/<name>/SKILL.md`, a sample
`.secondshift.yaml`, and setup instructions. Root `.agents/skills/<name>` symlinks
to the canonical skill for local agent discovery. Registered jobs in the root
manifest use canonical example paths directly.

Choose a skill below, then follow the [setup guide](../docs/engineering-tasks.md)
to copy it, connect data, approve access, and try it before enabling a schedule.
Individual examples provide service-specific setup and suggested cadences.

Skills are grouped by the role that usually owns them. Many are useful to
more than one team.

## Developers and code health

| Skill | What it does | Data needed |
| --- | --- | --- |
| [Repository health](repository-health/README.md) | Prioritizes code defects, unsafe configuration, and test gaps. | Repository source, configuration, project guidance, and tests. |
| [Documentation drift](docs-drift/README.md) | Finds docs that no longer match project files and behavior. | Documentation and the repository files it describes. |
| [CI failures](ci-failure-review/README.md) | Groups recurring failures and explains what still blocks builds. | GitHub Actions runs and job logs through the built-in GitHub connection, or another CI service you connect, plus repository code. |

## SRE and operations

| Skill | What it does | Data needed |
| --- | --- | --- |
| [Production errors](production-error-review/README.md) | Investigates new or worsening runtime errors and their impact. | Error events or traces, project/environment scope, and repository code. Sentry setup is included. |
| [Monitoring coverage](monitoring-coverage-review/README.md) | Finds failure paths left without useful detection. | Alert rules, routing, failure paths, and a baseline from committed configuration or a monitoring API. |
| [Incident follow-up](incident-follow-up-review/README.md) | Verifies completed actions against their promised outcome. | Incident actions, acceptance criteria, and implementation links from documents or a ticket API. |
| [On-call coverage](on-call-coverage/README.md) | Checks upcoming shifts for coverage gaps and conflicts with explicit time off. | Resolved on-call assignments, approved calendar or leave data, and team coverage rules. |

## Security and compliance

| Skill | What it does | Data needed |
| --- | --- | --- |
| [Security regression](security-regression/README.md) | Finds security defects introduced by recent changes. | Repository code and local or approved source-control history. Observability is optional. |
| [Quarterly access review](quarterly-access-review/README.md) | Flags accounts and permissions that owners should change, remove, or confirm, and shows unreviewed systems. | Workforce status, identity-provider and system access data, a system inventory, and access rules. |

## Release and engineering management

| Skill | What it does | Data needed |
| --- | --- | --- |
| [Release readiness](release-readiness-review/README.md) | Finds missing steps and blockers for a planned rollout. | An explicit release target, baseline, and rollout plan. |
| [Engineering briefing](engineering-briefing/README.md) | Summarizes shipped work, open blockers, and participation. | GitHub pull requests, reviews, and commits through approved read tools. |
| [Ticket follow-up brief](ticket-follow-up/README.md) | Finds tickets that may be ready to close or update and explains what still needs checking. | Repository history and approved tracker read access. |
| [Ticket reconciliation](ticket-reconciliation/README.md) | Updates or closes tickets that meet the team's completion rules. | Repository history, explicit workflow rules, and approved tracker read and write operations. |

## QA

| Skill | What it does | Data needed |
| --- | --- | --- |
| [First-time user review](first-time-user-review/README.md) | Walks a web app as five kinds of new user and ranks where they get stuck. | A browser MCP connection, a test copy of the app, and a prepared test account. |

## Reuse in your project

Copy the actual skill directory from its example into your project's
`.agents/skills`, rather than copying this repository's discovery symlink.
The skills work with repository files and approved service tools. They do not
assume SecondShift source code, a particular framework, or a Python runtime.
They can also run in a local coding agent with equivalent approved tools.
The engineering briefing is specifically for GitHub projects.

Historical comparisons need accessible history, and service investigations need
the relevant records. Missing history, logs, or telemetry is reported as a
coverage limitation. Individual examples describe their fallback behavior.

Only repository health, release readiness, docs drift, and engineering briefing
are registered in this repository's root manifest. Other sample jobs are
available to copy and review. They are not automatically registered.
Recurring examples propose schedules.
Release readiness stays manual because it needs an explicit release scope.

See the [setup guide](../docs/engineering-tasks.md) for the manual-first
workflow and connection approval steps.
