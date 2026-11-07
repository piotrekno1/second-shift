# Skill catalog

Ready-made skills for recurring engineering work. Copy one into your
repository, adapt it to your services and rules, and run it as a
[repository job](../docs/engineering-tasks.md).

Each example has the skill, a sample `.secondshift.yaml` and setup steps.
Skills are grouped by the team that usually owns them. Many are useful to more
than one team.

## Developers and code health

| Skill | What it does | Data needed |
| --- | --- | --- |
| [Repository health](repository-health/README.md) | Prioritizes code defects, unsafe configuration, and test gaps. | Repository source, configuration, project guidance, and tests. |
| [Django performance review](django-perf-review/README.md) | Traces and validates Django ORM performance problems. | Django source, tests, and any available query evidence. |
| [Documentation drift](docs-drift/README.md) | Finds docs that no longer match project files and behavior. | Documentation and the repository files it describes. |
| [Implicit state machines](implicit-state-machines/README.md) | Finds state machines the code has without naming them. | Repository source. |
| [CI failures](ci-failure-review/README.md) | Groups recurring failures and explains what still blocks builds. | GitHub Actions runs and job logs through the built-in GitHub connection, or another CI service you connect, plus repository code. |

## SRE and operations

| Skill | What it does | Data needed |
| --- | --- | --- |
| [Alert triage](alert-triage/README.md) | Triages an alert as it fires, from metrics, logs and errors around it. | The alert from a webhook trigger, plus Grafana and Sentry when connected. Grafana setup is included. |
| [Production errors](production-error-review/README.md) | Investigates new or worsening runtime errors and their impact. | Error events or traces, project/environment scope, and repository code. Sentry setup is included. |
| [Monitoring coverage](monitoring-coverage-review/README.md) | Finds failure paths left without useful detection. | Alert rules, routing, failure paths, and a baseline from committed configuration or a monitoring API. |
| [Incident follow-up](incident-follow-up-review/README.md) | Verifies completed actions against their promised outcome. | Incident actions, acceptance criteria, and implementation links from documents or a ticket API. |
| [On-call coverage](on-call-coverage/README.md) | Checks upcoming shifts for coverage gaps and conflicts with explicit time off. | Resolved on-call assignments, approved calendar or leave data, and team coverage rules. |

## Security and compliance

| Skill | What it does | Data needed |
| --- | --- | --- |
| [Security regression](security-regression/README.md) | Finds security defects introduced by recent changes. | Repository code and local or approved source-control history. Observability is optional. |
| [Security audit](security-audit/README.md) | Runs Cloudflare's security-audit skill one part of the codebase per week, with sub-agents verifying each finding. | Repository code and Cloudflare's skill, copied into the repository. |
| [Quarterly access review](quarterly-access-review/README.md) | Flags accounts and permissions that owners should change, remove, or confirm, and shows unreviewed systems. | Workforce status, identity-provider and system access data, a system inventory, and access rules. |

## Release and engineering management

| Skill | What it does | Data needed |
| --- | --- | --- |
| [Release readiness](release-readiness-review/README.md) | Finds missing steps and blockers for a planned rollout. | An explicit release target, baseline, and rollout plan. |
| [Engineering briefing](engineering-briefing/README.md) | Summarizes shipped work, open blockers, and participation. | GitHub pull requests, reviews, and commits through approved read tools. |
| [Ticket follow-up brief](ticket-follow-up/README.md) | Finds tickets that may be ready to close or update and explains what still needs checking. | Repository history and approved tracker read access. |
| [Ticket reconciliation](ticket-reconciliation/README.md) | Updates or closes tickets that meet the team's completion rules. | Repository history, explicit workflow rules, and approved tracker read and write operations. |

## Use a skill

1. Copy the skill directory from `examples/<name>/.agents/skills` into your
   repository's `.agents/skills`. Copy the directory itself, not this
   repository's discovery symlink.
2. Add the job to your `.secondshift.yaml` and commit both.
3. Connect the services it needs and approve its access.
4. Choose **Run now** and check the report before you turn on a schedule.

The skills work with any codebase and the tools an owner approves. They also
run in a local coding agent. When history, logs or telemetry are missing, a
skill reports the gap instead of treating it as a clean result.

See [repository jobs](../docs/engineering-tasks.md) for setup details and
[jobs that change service data](../docs/engineering-tasks.md#tasks-that-change-service-data).

In this repository, repository health, release readiness, Django performance
review, docs drift, implicit state machines, and the engineering briefing are
registered in the root manifest. The others are there to copy.
