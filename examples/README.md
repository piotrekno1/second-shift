# Reusable review examples

Each directory contains the canonical `.agents/skills/<name>/SKILL.md`, a sample
`.secondshift.yaml`, and setup instructions. Root `.agents/skills/<name>` symlinks
to the canonical skill for local agent discovery. Registered jobs in the root
manifest use canonical example paths directly.

Copy a skill's actual directory from its example into your project's
`.agents/skills` and merge the sample job into your root manifest. Do not copy
the root discovery symlink: its relative target belongs to this repository.
Skills can also run in a coding agent with equivalent approved tools, without
SecondShift. Sample manifests and connection setup are SecondShift-specific.

| Example | What it does | Data and scope |
| --- | --- | --- |
| [Repository health](repository-health/README.md) | Prioritizes code defects, unsafe configuration, and test gaps. | Language-independent; focused checks use already-installed dependencies. |
| [Documentation drift](docs-drift/README.md) | Finds docs that no longer match project files and behavior. | Uses the project's documentation conventions; generated paths and placeholders are not automatically broken links. |
| [CI failures](ci-failure-review/README.md) | Groups recurring failures and explains what still blocks builds. | Provider-neutral; configure actual CI read operations. GitHub commit access alone is insufficient. |
| [Production errors](production-error-review/README.md) | Investigates new or worsening runtime errors and their impact. | Sentry setup included; equivalent observability APIs can supply the evidence. |
| [Monitoring coverage](monitoring-coverage-review/README.md) | Finds failure paths left without useful detection. | Works from committed configuration or monitoring APIs; a snapshot cannot prove lost coverage. |
| [Incident follow-up](incident-follow-up-review/README.md) | Verifies completed actions against their promised outcome. | Works with committed incident documents or ticket APIs; does not assume a ticket vendor. |
| [Release readiness](release-readiness-review/README.md) | Finds missing steps and blockers for a planned rollout. | No framework or release-system assumption; a missing scope prevents a release verdict. |
| [Security regression](security-regression/README.md) | Finds security defects introduced by recent changes. | Local history or approved source-control tools; Sentry is optional. No history means a labeled snapshot review. |
| [Engineering briefing](engineering-briefing/README.md) | Summarizes shipped work, open blockers, and participation. | Reusable across GitHub projects. Deliberately retains GitHub semantics and tool operations. |

**Begin with a manual run, check its findings and coverage, then enable a
schedule if useful.** Proposed cadences are editable starting points.

## Reuse review

The skills can be reused in other repositories without SecondShift source
code, Django, Vue, or this repository's ownership rules. They discover project
guidance and evidence instead of imposing this project's conventions. The
engineering briefing remains GitHub-specific; generalizing away its PR and
commit semantics would lose useful precision.

The migration removes assumptions about host command-tool names, a mandatory
Python runtime, a scheduler always supplying time, and every environment being
an exported snapshot without dependencies. The reviews retain revision matching,
evidence links, distinctions between facts and hypotheses, explicit coverage
limits, and read-only behavior. Missing history, logs, or telemetry remains a
limitation rather than evidence that nothing is wrong.

Only repository health, release readiness, docs drift, and engineering briefing
are registered in this repository's root manifest. Their schedules and access
declarations are unchanged. Other sample jobs are available to copy and review;
they are not automatically registered. Recurring examples propose schedules;
release readiness stays manual because it needs an explicit release scope.

This is an instruction and configuration review, not a benchmark of model
performance across repositories. Validate a copied skill against a real project
with a manual run before enabling a schedule.
