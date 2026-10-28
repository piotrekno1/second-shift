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

https://github.com/user-attachments/assets/0d88e2ef-c14d-4bfa-8367-7a5c4e4fd627

Commit a skill and its schedule alongside your code, connect a model, and
approve the tools it may use. Skills can read data and take actions through
approved MCP tools or HTTP API operations. Results can arrive in Slack or another
webhook destination. Commands run in an isolated sandbox. Each run records its Git
revision, tool activity, and model cost.

## Try it out now

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) to manage the
application and start Docker for job sandboxes, then run:

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

## Put recurring tasks on duty

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

Each workspace role can also do everything the roles below it can. The full
list of actions and the role each needs is `POLICY` in
`secondshift/workspaces/access.py`.

A committed skill does not grant itself access. Repository configuration cannot
choose credentials, provider endpoints, sandbox images, mounts, or network
settings. Expanding requested capabilities requires renewed approval.

Skills run in isolated sandboxes. Every run is tied to a Git commit.

## Why use SecondShift?

### Why add SecondShift if your agent already supports schedules?

Review skills and schedules in Git, approve service access per job, and share
run history across repositories. Each run records its Git revision, tool
activity, report, and model cost. Owners grant access, admins operate jobs,
and members view results.

SecondShift is open source under [Apache 2.0](LICENSE). Inspect how permissions
and sandbox isolation work, self-host it, and configure models, connections,
and runtime limits.

Fresh, networkless sandboxes suit repository checks. Jobs can use earlier
successful reports as
context through the [`history` setting](docs/repository-configuration.md#run-history).
Sandbox files and sessions do not persist between runs. Work that needs a
browser or persistent sessions may fit another assistant better.

Compare [SecondShift, Grok Bot, Hermes Agent, and Viktor](docs/comparisons.md),
or read the [security model](docs/security-model.md).

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

- [Comparisons](docs/comparisons.md): when to choose SecondShift, Grok Bot, Hermes Agent, or Viktor.
- [Repository configuration](docs/repository-configuration.md): skills, YAML syntax, schedules, and notifications.
- [Engineering tasks](docs/engineering-tasks.md): skill setup, approved service actions, and scheduling.
- [Environment variables](docs/environment-variables.md): service-scoped configuration and setup file generation.
- [Architecture](docs/architecture.md): execution design and capability boundaries.
- [Security model](docs/security-model.md): what a run can reach, where secrets live, and remaining risks.
- [Self-hosting](docs/self-hosting.md) (work in progress): runner backends, sandbox images, and artifact storage.
- [Contributing](CONTRIBUTING.md): source setup, development, verification, and releases.
