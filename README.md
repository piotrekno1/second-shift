<h1>
  <img src="dashboard/public/secondshift-mark.svg" alt="" width="40" height="40" align="absmiddle">
  SecondShift
</h1>

[![CI](https://github.com/piotrekno1/second-shift/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/piotrekno1/second-shift/actions/workflows/ci.yml?query=branch%3Amain)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

**Same agent. Same task. Every week?**  
**Put it on a schedule with SecondShift.**

Give your coding agents recurring tasks: investigate CI failures, check release
readiness, or close fixed issues through an approved service connection.
Commit a skill, set a schedule, and follow what gets done.

Your team controls their access. Every run leaves an audit trail.

https://github.com/user-attachments/assets/73cbd504-2a13-4a99-bf95-d7a5c3745e28

Commit a skill and its schedule alongside your code, connect a model, and
approve the tools it may use. Skills can read data and take actions through
approved MCP tools or HTTP API operations. Results can arrive in Slack or another
webhook destination. Commands run in an isolated sandbox. Each run records its Git
revision, tool activity, and model cost.

## Try it out now

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and start Docker,
then run:

```bash
# Install SecondShift
release=https://github.com/piotrekno1/second-shift/releases
uv tool install --force --python 3.13 \
  "$release/download/v0.1.0a6/secondshift-0.1.0a6-py3-none-any.whl"

# Start SecondShift
secondshift up
```

Upgrading from an earlier alpha? Read
[installed builds](CONTRIBUTING.md#installed-builds-and-troubleshooting) first.

Once started, the console shows:

```text
SecondShift is running at http://127.0.0.1:8000
Sign in with this link. It works once, within 15 minutes:
  http://127.0.0.1:8000/-/sign-in/<one-time-token>/
Run `secondshift login` for a new link. Press Ctrl+C to stop.
```

SecondShift is in alpha, distributed through
[GitHub Releases](https://github.com/piotrekno1/second-shift/releases) until it is
available on PyPI.

## What you can do

- **See what is on duty.** The control room shows upcoming shifts, recent runs,
  failures, and model spend.
- **Manage schedules from Git.** Discover jobs from `.secondshift.yaml`, review
  their skills and schedules, and enable or run them manually.
- **Connect models and report destinations.** Add a model provider and webhook,
  then approve each job's notification access. Connections remain separately
  revocable.
- **Connect skills to your services.** Connect GitHub, Sentry, any MCP server (OAuth or a
  token), or an HTTP API from its OpenAPI spec. Choose which tools jobs may use,
  then approve each job's request, including operations that change data.
  Calls run outside the sandbox. Built-in GitHub and Sentry tools are read-only.
- **Follow every run.** Inspect its Git revision, execution timeline, report,
  token usage, and model cost.

## Put recurring tasks on duty

Write a skill for recurring work your team needs done. Connect
the services it needs through MCP or an HTTP API, then approve the job's access.

The current task examples focus on reviews and briefings:

- [Documentation drift](examples/docs-drift/README.md) finds broken links and
  stale commands in your docs. It needs only a model provider.
- [CI failure review](examples/ci-failure-review/README.md) groups recurring
  build failures and identifies what still blocks work.
- [Security regression review](examples/security-regression/README.md) reviews
  recent changes for security defects, with optional runtime context.

Browse the [full skill catalog](examples/README.md) to choose a workflow and see
what data it needs. Follow the [engineering task setup guide](docs/engineering-tasks.md)
to copy a skill, approve access, try it manually, and enable scheduling.

You can also write action skills. For example, a Jira task could check whether
issues meet your definition of fixed, then transition them through an approved
custom MCP connection. Define the completion criteria, allowed actions, and how
to handle an item that was already processed. See
[tasks that change service data](docs/engineering-tasks.md#tasks-that-change-service-data).

## Who controls what

| Role | Controls |
| --- | --- |
| **Repository contributors** | Propose skills, schedules, and requested capabilities through Git changes. |
| **Repository maintainers** | Review and merge those changes through the repository's normal process. |
| **Workspace owners** | Connect model providers and services, approve each job's access, add local repositories, and invite people and manage their roles. |
| **Workspace admins** | Enable, run and schedule approved jobs, manage repositories, and change run limits. |
| **Workspace members** | View repositories, jobs and runs. |
| **Server operators** | Define sandbox images, runtimes, resource limits, and network policy. |

Each workspace role can also do everything the roles below it can. The full
list of actions and the role each needs is `POLICY` in
`secondshift/workspaces/access.py`.

A committed skill does not grant itself access. Repository configuration cannot
choose credentials, provider endpoints, sandbox images, mounts, or network
settings. Expanding requested capabilities requires renewed approval.

Skills run in isolated sandboxes. Every run is tied to a Git commit.

## Why use SecondShift?

### How is this different from Hermes Agent?

[Hermes Agent](https://hermes-agent.nousresearch.com/docs) is individual-first,
centered on an agent that remembers your context and works across your tools.
SecondShift is team-first, built around recurring engineering tasks managed
through Git, explicit permissions, and shared run history. Both can serve
individuals or teams.

Hermes already supports scheduled skills and report delivery. In SecondShift,
review the skill and schedule in Git, approve its service access separately,
and inspect each run's revision, tool activity, report, and cost. Workspace
roles separate who can grant access, operate jobs, and view results.

A merged skill cannot grant itself credentials or expand its approved access.
Commands run in a networkless sandbox. External tool calls go through a broker
that checks the job's approved permissions. See the
[security model](docs/security-model.md) for the boundaries and remaining risks.

### Why not use an existing scheduler?

An existing scheduler can start an agent on a timer. SecondShift also handles
job discovery from Git, access approvals, sandbox execution, run history, model
cost tracking, and report delivery. It gives teams one place to manage that work
across repositories.

## Contributions welcome

SecondShift is early, and contributions are welcome: bug reports, ideas,
documentation improvements, and pull requests. See
[CONTRIBUTING.md](CONTRIBUTING.md) to get started. For larger changes,
[open an issue](https://github.com/piotrekno1/second-shift/issues) first so we can
discuss the approach.

## Documentation

- [Repository configuration](docs/repository-configuration.md): skills, YAML syntax, schedules, and notifications.
- [Engineering tasks](docs/engineering-tasks.md): skill setup, approved service actions, and scheduling.
- [Architecture](docs/architecture.md): execution design and capability boundaries.
- [Security model](docs/security-model.md): what a run can reach, where secrets live, and remaining risks.
- [Self-hosting](docs/self-hosting.md) (work in progress): runner backends, sandbox images, and artifact storage.
- [Contributing](CONTRIBUTING.md): source setup, development, verification, and releases.
