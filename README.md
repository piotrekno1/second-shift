<h1>
  <img src="dashboard/public/secondshift-mark.svg" alt="" width="40" height="40" align="absmiddle">
  SecondShift
</h1>

[![CI](https://github.com/piotrekno1/second-shift/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/piotrekno1/second-shift/actions/workflows/ci.yml?query=branch%3Amain)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

**GitOps for recurring agent work.**

SecondShift turns committed repository skills into scheduled reviews and reports.

<div align="center">

https://github.com/user-attachments/assets/139dc763-e7eb-4e1f-ac27-c2f44bac452c

</div>

Connect a model, add a webhook, import a repository, and receive a skill's report.

## Try it out now

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and start Docker,
then run:

```bash
# Install SecondShift
release=https://github.com/piotrekno1/second-shift/releases
uv tool install --python 3.13 \
  "$release/download/v0.1.0a2/secondshift-0.1.0a2-py3-none-any.whl"

# Start SecondShift
secondshift up
```

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
- **Follow every run.** Inspect its Git revision, execution timeline, report,
  token usage, and model cost.

## Put recurring reviews on duty

- **Release recap:** summarize merged features, fixes, and rollout questions.
- **CI failure review:** group test failures and suggest fixes from logs.
- **Repository health:** find defects, test gaps, and outdated documentation.
- **Contributor recap:** recognize merged work and review contributions.
- **Alert coverage review:** flag removed alerts, paused rules, and threshold changes.

Skills review the inputs you provide. Service data currently needs to be
exported into the repository. MCP and API tool execution is not available yet.

See [Repository configuration](docs/repository-configuration.md) to add your
first skill, schedule, and webhook notification.

## Who controls what

| Role | Controls |
| --- | --- |
| **Repository contributors** | Propose skills, schedules, and requested capabilities through Git changes. |
| **Repository maintainers** | Review and merge those changes through the repository's normal process. |
| **Workspace owners** | Connect model providers and services, approve each job's access, and enable jobs. |
| **Server operators** | Define sandbox images, runtimes, resource limits, and network policy. |

A committed skill does not grant itself access. Repository configuration cannot
choose credentials, provider endpoints, sandbox images, mounts, or network
settings. Expanding requested capabilities requires renewed approval.

Skills run in isolated sandboxes. Every run is tied to a Git commit.

## Contributions welcome

SecondShift is early, and contributions are welcome: bug reports, ideas,
documentation improvements, and pull requests. See
[CONTRIBUTING.md](CONTRIBUTING.md) to get started. For larger changes,
[open an issue](https://github.com/piotrekno1/second-shift/issues) first so we can
discuss the approach.

## Documentation

- [Repository configuration](docs/repository-configuration.md): skills, YAML syntax, schedules, and notifications.
- [Architecture](docs/architecture.md): execution design and capability boundaries.
- [Self-hosting](docs/self-hosting.md): runner backends, sandbox images, and artifact storage.
- [Contributing](CONTRIBUTING.md): source setup, development, verification, and releases.
