<h1>
  <img src="dashboard/public/secondshift-mark.svg" alt="" width="40" height="40" align="absmiddle">
  SecondShift
</h1>

[![CI](https://github.com/piotrekno1/second-shift/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/piotrekno1/second-shift/actions/workflows/ci.yml?query=branch%3Amain)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

**Recurring engineering tasks, managed by your team.**

SecondShift lets engineering teams schedule AI tasks across their repositories:
investigate CI failures, check release readiness, and find documentation drift.

Keep procedures in Git, approve the tools each task can use, and set token limits
for every run. Your team shares the results, execution history, and model costs.

Open source and self-hosted, with your choice of model provider.

## Why use SecondShift?

| Your team's problem | How SecondShift helps |
| --- | --- |
| Checks get skipped when nobody remembers to run them. | Schedule a procedure stored alongside your code. |
| Automation lives in one person's setup. | Share jobs, schedules, and run history with the team. |
| Agents need access to company services. | Owners approve each job's tools. Broader access requires renewed approval. |
| Agent usage needs limits and visibility. | Set per-run token limits and inspect usage and model cost. |
| You need to understand what happened. | Inspect the Git revision, tool activity, and report for each run. |

## See it in action

Schedule a CI failure review every Monday. The task reads approved
build history, connects recurring failures to repository code, and delivers a
report to your team's Slack channel. Open the run to review its findings, tool
activity, and model cost.

A **skill** is the written procedure the agent follows. Commit it and its
schedule to Git so your team can review changes before they take effect.

https://github.com/user-attachments/assets/0d88e2ef-c14d-4bfa-8367-7a5c4e4fd627

Browse the [skill catalog](examples/README.md) for documentation
drift, release readiness, and ticket follow-up. Skills can also take actions,
such as updating tickets, through explicitly approved service tools.

## Access, budgets, and credentials

- **Your team controls access.** Workspace owners connect services and approve
  each job's tools. Admins operate approved jobs and change run limits. Members
  can view jobs and results. Expanding a job's requested access requires renewed
  approval.
- **Each run has limits.** Set workspace defaults and job overrides for token
  usage, duration, model turns, and tool calls, within server-defined ceilings.
  Token budgets apply per run. Each run records its model cost. See
  [run limits](docs/self-hosting.md) for enforcement details.
- **You host the credentials.** SecondShift stores them encrypted and uses them
  to make approved calls outside the sandbox. It does not pass configured service
  credentials to the model or sandbox. The server operator has access to them.
  Your model provider receives the context sent to it. See the
  [security model](docs/security-model.md).

[Try it locally](#try-it-locally) or [deploy for your team](#deploy-for-your-team).

## Try it locally

Run the included documentation review on this repository and inspect its report
and cost.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and start
Docker, then run:

```bash
# Install SecondShift
release=https://github.com/piotrekno1/second-shift/releases
uv tool install --force --python 3.13 \
  "$release/download/v0.1.0b3/secondshift-0.1.0b3-py3-none-any.whl"

# Start SecondShift
secondshift up
```

Upgrading from an alpha? This beta resets database migrations and requires a
fresh database. Read the [beta release notes](docs/releases/0.1.0b3.md) first.

Once started, the console shows:

```text
SecondShift is running at http://127.0.0.1:8000
Sign in with this link. It works once, within 15 minutes:
  http://127.0.0.1:8000/-/sign-in/<one-time-token>/
Run `secondshift login` for a new link. Press Ctrl+C to stop.
```

SecondShift is in beta, distributed through
[GitHub Releases](https://github.com/piotrekno1/second-shift/releases) until it is
available on PyPI.

### Let SecondShift review itself

In another terminal, clone this repository:

```bash
git clone https://github.com/piotrekno1/second-shift.git
```

1. In **Connections**, add a model provider and choose a default model. Model
   usage may incur charges.
2. In **Repositories**, choose **Add local repository** and select the cloned
   directory, or enter its absolute path.
3. Open **Documentation drift** in **Jobs** and click **Run now**.
4. Inspect the report, execution timeline, and model cost.

The repository already includes the skill and configuration.

## Deploy for your team

For a shared installation on a Linux server, follow the
[Docker Compose deployment guide](docs/production-deployment.md). The example
sets up Caddy with automatic HTTPS, PostgreSQL, and a gVisor runner, and connects
to your private S3-compatible bucket. The guide covers GitHub App configuration,
initial setup, backups, and updates.

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

## Set up your team's tasks

Browse the [skill catalog](examples/README.md) to choose a workflow for your
own repository. Follow the [engineering task setup guide](docs/engineering-tasks.md)
to copy a skill, approve access, try it manually, and enable scheduling.

## Who controls what

| Role | Controls |
| --- | --- |
| **Repository contributors** | Propose skills, schedules, and requested capabilities through Git changes. |
| **Repository maintainers** | Review and merge those changes through the repository's normal process. |
| **Workspace owners** | Connect model providers and services, approve each job's access, add local repositories, and invite people and manage their roles. |
| **Workspace admins** | Enable, run and schedule approved jobs, manage repositories, and change run limits. |
| **Workspace members** | View repositories, jobs and runs. |
| **Server operators** | Define sandbox images, runtimes, resource limits, and network policy. |

Workspace owners can also perform admin and member actions. Admins can also
perform member actions. The full list of actions and required roles is `POLICY` in
`secondshift/workspaces/access.py`.

A committed skill does not grant itself access. Repository configuration cannot
choose credentials, provider endpoints, sandbox images, mounts, or network
settings. Expanding requested capabilities requires renewed approval.

Skills run in isolated sandboxes. Every run is tied to a Git commit.

## How does it fit with other tools?

### Why add SecondShift if your agent already supports schedules?

Use SecondShift when your team needs to review task definitions, control access,
set run limits, and track results across repositories.

Compare [SecondShift, Grok Bot, Hermes Agent, and Viktor](docs/comparisons.md),
or read the [security model](docs/security-model.md).

### Why not use an existing scheduler?

An existing scheduler can start an agent on a timer. SecondShift also handles
job discovery from Git, access approvals, sandbox execution, run history, model
cost tracking, and report delivery across repositories.

## Contributions welcome

See [CONTRIBUTING.md](CONTRIBUTING.md) for bug reports, documentation changes, and
pull requests. For larger changes,
[open an issue](https://github.com/piotrekno1/second-shift/issues) first so we can
discuss the approach.

## Documentation

- [Comparisons](docs/comparisons.md): when to choose SecondShift, Grok Bot, Hermes Agent, or Viktor.
- [Repository configuration](docs/repository-configuration.md): skills, YAML syntax, schedules, and notifications.
- [Engineering tasks](docs/engineering-tasks.md): skill setup, approved service actions, and scheduling.
- [Environment variables](docs/environment-variables.md): service-scoped configuration and setup file generation.
- [Architecture](docs/architecture.md): execution design and capability boundaries.
- [Security model](docs/security-model.md): what a run can reach, where secrets live, and remaining risks.
- [Self-hosting](docs/self-hosting.md) (work in progress): runner backends, sandbox images, and artifact storage.
- [Contributing](CONTRIBUTING.md): source setup, development, verification, and releases.
