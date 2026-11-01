<h1>
  <img src="dashboard/public/secondshift-mark.svg" alt="" width="40" height="40" align="absmiddle">
  SecondShift
</h1>

[![CI](https://github.com/piotrekno1/second-shift/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/piotrekno1/second-shift/actions/workflows/ci.yml?query=branch%3Amain)
[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)

**Give recurring engineering work a shift.**

Commit an agent skill next to your code. SecondShift runs it in a sandbox,
with only the access your team approved, and keeps its results and cost.

Open source, self-hosted, and in beta. Bring your own model provider.

[Quick start](#quick-start) · [Skill catalog](examples/README.md) ·
[Docs](https://second-shift.app/docs/overview/) · [Website](https://second-shift.app/)

https://github.com/user-attachments/assets/0a9a7baa-f79e-4934-af81-8e5374e9cdbf

## How it works

Define a job in `.secondshift.yaml`. This example runs a skill every Monday:

```yaml
jobs:
  engineering-briefing:
    name: Monday engineering briefing
    skill: examples/engineering-briefing/.agents/skills/engineering-briefing/SKILL.md
    schedule: "0 9 * * 1"
    requires:
      mcps:
        - binding: github
          tools:
            - search_pull_requests
            - list_commits
    notify:
      - binding: engineering-reports
```

1. **Write** the skill with Claude Code, Codex, or any coding agent. Merge it
   through your normal review.
2. **Approve** its access in the dashboard. A repository can ask for tools. Only
   a workspace owner can grant them.
3. **Run skills when you need them.** Start a run yourself, set a schedule, or
   trigger it from another service. Each run executes in an isolated sandbox
   with approved access.
4. **Review** the results in the dashboard, Slack, or a webhook. Every run records
   its Git commit, tool calls, and model cost.

## Why SecondShift

- **Checks happen even when nobody remembers.** Investigate CI failures every
  morning or review release readiness every Friday.
- **The whole team shares it.** Jobs, schedules, and run history live in one
  place, not in one person's setup.
- **Access is approved, not assumed.** Credentials stay encrypted on your server
  and never reach the model or the sandbox.
- **Spending has limits.** Set per-run token and time limits and see what each
  run cost.

[Compare SecondShift with Hermes Agent and other tools](https://second-shift.app/docs/comparisons/).

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker,
and a model provider API key. Start Docker, then run:

```bash
release=https://github.com/piotrekno1/second-shift/releases
uv tool install --force --python 3.13 \
  "$release/download/v0.1.0b7/secondshift-0.1.0b7-py3-none-any.whl"
secondshift up
```

Open the sign-in link printed in the terminal. Then let SecondShift review its
own documentation:

1. Clone this repository: `git clone https://github.com/piotrekno1/second-shift.git`
2. In **Connections**, add a model provider.
3. In **Repositories**, choose **Add local repository** and select the clone.
4. In **Jobs**, open **Documentation drift** and click **Run now**.

## Deploy for your team

Follow the [Docker Compose deployment guide](https://second-shift.app/docs/production-deployment/)
for a shared Linux server with HTTPS, a GitHub App, and backups. Then pick a task
from the [skill catalog](examples/README.md) and follow the
[task setup guide](https://second-shift.app/docs/engineering-tasks/).

## Use it from your coding agent

SecondShift serves an MCP server at `/mcp`. Your agent can check a
`.secondshift.yaml` before you commit it, start approved jobs, and read results.
It cannot approve access or see credentials. See the
[MCP guide](https://second-shift.app/docs/mcp/).

## Learn more

- [Repository configuration](https://second-shift.app/docs/repository-configuration/): YAML syntax, schedules, and notifications
- [Security model](https://second-shift.app/docs/security-model/): what a run can reach and where secrets live
- [Self-hosting](https://second-shift.app/docs/self-hosting/): roles, run limits, runners, and storage
- [Full documentation](https://second-shift.app/docs/overview/)

## Contributing

Bug reports, docs fixes, and pull requests are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md). For larger changes,
[open an issue](https://github.com/piotrekno1/second-shift/issues) first.
