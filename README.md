<h1 align="center">
  <a href="https://second-shift.app/"><img src="dashboard/public/secondshift-mark.svg" width="64" height="64" alt="SecondShift homepage"></a>
  <br>
  SecondShift
</h1>

<p align="center"><strong>Self-hosted agent jobs for recurring engineering work.</strong></p>

<p align="center">
  <a href="https://github.com/piotrekno1/second-shift/actions/workflows/ci.yml"><img src="https://github.com/piotrekno1/second-shift/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI status on main"></a>
  <a href="https://pypi.org/project/secondshift/"><img src="https://img.shields.io/pypi/v/secondshift" alt="PyPI version"></a>
</p>

<p align="center">
  <a href="#quick-start">Quick start</a> ·
  <a href="examples/README.md">Skill catalog</a> ·
  <a href="docs/security-model.md">Security model</a> ·
  <a href="https://second-shift.app/docs/overview/">Docs</a>
</p>

Try a skill on your repository, then run it **on demand**, **on a cron schedule**,
or **from a webhook**. Jobs live in `.secondshift.yaml`.

The agent runs in a **sandbox without direct network access**. A broker
enforces which tools it can call, and service credentials stay outside the sandbox.

https://github.com/user-attachments/assets/2e48124d-489f-449b-be66-25107697e9b0

**CI failures → draft fix → scheduled skill.**

## Quick start

Start Docker and install [uv](https://docs.astral.sh/uv/getting-started/installation/).
You'll need a model API key or a [Claude Code or Codex subscription](docs/agent-subscriptions.md).

```bash
uv tool install secondshift
secondshift up
```

1. Clone this repo with `git clone https://github.com/piotrekno1/second-shift.git`.
2. In the dashboard, add the clone under **Repositories → Add repository → Local folder**.
3. In **Models**, connect your model. Then open **Jobs → Documentation drift**
   and click **Run now**.

To try another skill, open **Explore**. The catalog shows what each skill
produces, what it can access, and what it can change. For your own skill or
one from GitHub, see [one-off tasks](docs/tasks.md).

## A job in Git

This job runs the repository's documentation review every Monday at 08:00 UTC.

```yaml
version: 1
jobs:
  docs-drift:
    name: Documentation drift
    skill: examples/docs-drift/.agents/skills/docs-drift/SKILL.md
    schedule: "0 8 * * 1"
    timezone: UTC
```

A skill is a `SKILL.md` file with instructions for the agent. You can keep it
in your repo or reference a GitHub skill pinned to a commit. The
[configuration reference](docs/repository-configuration.md) covers webhook
triggers, requested tools, and delivery.

- **Access.** A workspace owner approves the model and tools. The broker
  checks each call against the job's approved access, including argument
  limits. Approving a write tool grants the agent that write capability.
- **Output.** Inspect the report, source commit, tool calls, and token
  usage in run history. Send reports to Slack or a webhook, or configure
  GitHub issues and draft PRs.
- **MCP.** Start approved jobs, submit tasks, or read reports from your
  coding agent. [Connect an MCP client](docs/mcp.md)

## Deploy and contribute

SecondShift is in beta. The [production deployment guide](docs/production-deployment.md)
covers Docker Compose, PostgreSQL, gVisor, and backups.

[Architecture](docs/architecture.md) · [Release notes](docs/releases/) ·
[Contributing](CONTRIBUTING.md)
