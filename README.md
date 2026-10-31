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

Open source, self-hosted, and currently in beta. Use your choice of model provider.

[Watch the demo](#see-it-in-action) · [Try it locally](#try-it-locally) ·
[Use it with your team](#use-it-with-your-team)

## Why use SecondShift?

| Your team's problem | How SecondShift helps |
| --- | --- |
| Checks get skipped when nobody remembers to run them. | Schedule a procedure stored alongside your code. |
| Automation lives in one person's setup. | Share jobs, schedules, and run history with the team. |
| Agents need access to company services. | Owners approve each job's tools. Broader access requires renewed approval. |
| Agent usage needs limits and visibility. | Set per-run token limits and inspect usage and model cost. |
| You need to understand what happened. | Inspect the Git revision, tool activity, and report for each run. |

An existing scheduler can start an agent on a timer. SecondShift adds access
approvals, per-run limits, sandbox execution, shared run history, and cost
tracking across repositories.
[Compare SecondShift with other tools](docs/comparisons.md).

## See it in action

Follow an infrastructure review from versioned instructions and approved tools
to a report delivered in Slack. Inspect job history, run limits, tool activity,
and model cost.

https://github.com/user-attachments/assets/0a9a7baa-f79e-4934-af81-8e5374e9cdbf

## Access, budgets, and credentials

- **Access:** Workspace owners approve each job's tools. Broader access requires
  renewed approval. See [roles and permissions](docs/self-hosting.md#who-controls-what).
- **Budgets:** Set per-run token and execution limits. Each run records usage and
  model cost. See [run limits](docs/self-hosting.md#run-limits).
- **Credentials:** Your installation stores them encrypted and uses them outside
  the sandbox. Configured credentials are not passed to the model or sandbox.
  The server operator has access to them. Your model provider receives the context
  sent to it. See the [security model](docs/security-model.md).

## Try it locally

Run the included documentation review on this repository and inspect its report
and cost.

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker,
Git, and a model provider. Model usage may incur charges. Start Docker, then run:

```bash
# Install SecondShift
release=https://github.com/piotrekno1/second-shift/releases
uv tool install --force --python 3.13 \
  "$release/download/v0.1.0b3/secondshift-0.1.0b3-py3-none-any.whl"

# Start SecondShift
secondshift up
```

Open the sign-in link printed in the terminal.
[Local sign-in and upgrade details](docs/self-hosting.md#local-trial).

In another terminal, clone this repository:

```bash
git clone https://github.com/piotrekno1/second-shift.git
```

1. In **Connections**, add a model provider and choose a default model.
2. In **Repositories**, choose **Add local repository** and select the cloned
   directory, or enter its absolute path.
3. Open **Documentation drift** in **Jobs** and click **Run now**.
4. Inspect the report, execution timeline, and model cost.

The repository already includes the skill and configuration.

## Use it with your team

For a shared installation on a Linux server, follow the
[Docker Compose deployment guide](docs/production-deployment.md). It covers HTTPS,
GitHub App configuration, backups, and updates.

A **skill** is the written procedure the agent follows. Commit it and its
schedule to Git so your team can review changes before they take effect.

Choose a task from the [skill catalog](examples/README.md), then follow the
[task setup guide](docs/engineering-tasks.md) to approve access, test a run, and
turn on scheduling. Tasks can report findings or update connected services
through approved tools.

## Use it from your coding agent

SecondShift serves an MCP server at `/mcp`. Create a token under
**API tokens** in the account menu and paste the setup command it shows. Your
agent can then read runs and reports, start approved jobs, and check a
`.secondshift.yaml` before you commit it. It cannot approve access or see
credentials. See the [MCP guide](docs/mcp.md).

## Documentation

- [Comparisons](docs/comparisons.md): when to choose SecondShift, Grok Bot, Hermes Agent, or Viktor.
- [Repository configuration](docs/repository-configuration.md): skills, YAML syntax, schedules, and notifications.
- [Engineering tasks](docs/engineering-tasks.md): skill setup, approved service actions, and scheduling.
- [Environment variables](docs/environment-variables.md): service-scoped configuration and setup file generation.
- [MCP server](docs/mcp.md): connect coding agents, API tokens, and the tools they can use.
- [Architecture](docs/architecture.md): execution design and capability boundaries.
- [Security model](docs/security-model.md): what a run can reach, where secrets live, and remaining risks.
- [Self-hosting](docs/self-hosting.md) (work in progress): runner backends, sandbox images, and artifact storage.
- [Contributing](CONTRIBUTING.md): source setup, development, verification, and releases.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for bug reports, documentation changes, and
pull requests. For larger changes,
[open an issue](https://github.com/piotrekno1/second-shift/issues) first to discuss
the approach.
