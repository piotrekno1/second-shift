# SecondShift

**Unattended agent jobs for your team, without giving the agent your service
credentials.**

Define a job once in your repository. SecondShift runs it on a schedule, when
an alert or other webhook arrives, or when anyone on the team asks from the
dashboard or their coding agent. The agent works in a sandbox with no network
and no service credentials. Every tool call goes through a broker that checks
it against what a workspace owner approved. Results land where your team
already works, such as Slack, a GitHub issue or a draft PR.

Built for platform, security and SRE teams who want agents doing recurring
work without handing them the keys. Runs on your laptop with one command, or
on your own server in production.

[Quick start](#quick-start) · [Skill catalog](examples/README.md) ·
[Security model](docs/security-model.md) ·
[Docs](https://second-shift.app/docs/overview/)

https://github.com/user-attachments/assets/69cbc92f-72f6-45fb-98ad-27b4b1056581

## Quick start

You need [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker,
and a model API key or a Claude Code or Codex subscription. Start Docker, then
run:

```bash
uv tool install --python 3.13 secondshift
secondshift up
```

The first start builds the agent image for subscriptions and takes a few
minutes. Use `secondshift up --no-agents` to skip it. Your dashboard opens when
SecondShift is ready.

Try it on this repository. SecondShift reviews its own documentation:

1. Clone it: `git clone https://github.com/piotrekno1/second-shift.git`
2. In **Repositories**, choose **Add repository**, then **Local folder**, and
   select the clone.
3. In **Models**, add an API key or a Claude Code or Codex subscription.
4. In **Jobs**, open **Documentation drift** and click **Run now**.

For one-off work, open a repository, click **New task** and write what to do.
To connect your coding agent, open **Connect your agent** in the account menu.

### Try a skill from GitHub in the sandbox

A skill from the internet is someone else's instructions, and many come with
scripts. Run one in your own coding agent and those scripts can reach whatever
your machine can. In SecondShift its commands run in the sandbox, with no
network and no credentials.

Open a repository, click **New task**, then **Skill from GitHub**, and paste a
link to a `SKILL.md`, such as one from
[anthropics/skills](https://github.com/anthropics/skills). SecondShift pins it
to a commit, shows what it asks for, and mounts it read-only. If you like the
result, click **Run again** or **Schedule this skill**. See
[Try a skill from GitHub](docs/tasks.md#try-a-skill-from-github).

## The problem

Your team asks agents for the same work every week. Triage the CI failures.
Check which docs went stale. Look into the alert that fired at 3am. Each time,
someone writes the prompt again, waits for the answer and pays for the tokens.
Often two people pay for the same answer.

Automating it means letting agents run unattended. The usual way is a cron
workflow in CI. The agent runs on the runner that holds your keys, with open
network access. One prompt injection in a log line or an issue reaches
everything that runner can. Token spend has no ceiling, and each repository
wires up its own workflow.

So teams keep prompting by hand, or accept these risks.

With SecondShift you define the work once. It runs on a schedule or when an
event arrives, once for the whole team. The agent never holds your service
credentials, and it can call only the tools an owner approved. The latest result is
already waiting in Slack, or in your coding agent over MCP.

## Who it's for

Platform, security and SRE teams at companies that already use agents and now
want them to run unattended. SecondShift fits when:

- your security review won't let an agent hold production credentials,
- several teams want recurring jobs with one set of approvals and one record,
- you need to choose the model or keep data on your own servers.

For a few personal jobs, Claude Code scheduled tasks or a GitHub Actions
workflow may be enough. See [Alternatives](docs/comparisons.md).

## How it works

A job is a skill plus a few lines of YAML in `.secondshift.yaml`:

```yaml
jobs:
  alert-triage:
    name: Alert triage
    skill: .agents/skills/alert-triage/SKILL.md
    triggers: [webhook]
    webhook:
      skip_when: { status: resolved }
      repeat_key: groupKey
      repeat_window: 1h
    requires:
      mcps:
        - binding: grafana
          tools: [query_prometheus, query_loki_logs, alerting_rules_read]
        - binding: sentry
          tools: [search_issues, search_errors]
    notify:
      - binding: alerts-slack
```

1. **Merge it.** SecondShift syncs the repository and finds the job.
2. **Approve access.** The job asks for tools by name. A workspace owner
   decides which connection provides them and can restrict their arguments.
   The YAML never holds a credential, endpoint or token.
3. **It runs.** On a schedule, from a webhook, or on demand. The agent works in
   a networkless sandbox on a snapshot of the commit. Tool calls go through a
   broker that checks each one against the approved plan.
4. **It delivers.** SecondShift posts the report, opens the issues or the draft
   pull request, and keeps the run's full record.

## What you get

**Work defined in Git.** Skills and schedules are files in the repository.
Changes go through pull requests and code owners. Every run records the commit
it ran and the exact access it had.

**Access an owner approves.** Repositories request capabilities. Owners grant
them per job, per tool, and can fix, limit or forbid individual arguments. An
owner can also make the merge itself the approval for tools a connection
already allows.

**No secrets in the sandbox.** The agent runs with no network and no
credentials. SecondShift makes tool calls, opens pull requests and files
issues on its behalf. The model is always isolated from keys.

**Three ways to start work.**
- *Schedules* for recurring reviews and briefings.
- *Webhooks* for events such as alerts, with signed requests and filters for
  resolved and repeated alerts.
- *One-off tasks* from the dashboard or your coding agent, queued per
  repository and optionally delayed. A task can run a skill from any public
  GitHub repository, pinned to a commit.

**Output your team can act on.** Reports to Slack or any webhook, with retry.
Files the agent saved, such as CSVs and images. GitHub issues, deduplicated and
capped per run. Draft pull requests on a `secondshift/run-<id>` branch. Jobs
can read their own earlier reports, so a weekly review knows what it said last
week.

**Your choice of model.** Use an API key from a model provider, or a local
model. You can also run jobs through the official Claude Code or Codex CLI,
signed in with your own account. Check that your plan's terms cover it. For
team jobs in production, use an API key.

**Costs with a ceiling.** Each run has a token budget and a tool-call limit.
The gateway cuts a run off when its budget is used.

**History you can see.** Each run shows its trigger, commit, tool calls, token
usage and model cost. Runs are durable workflows. They survive
restarts, and retries don't repeat tool calls.

**Works with your coding agent.** SecondShift is an MCP server. Claude Code,
Codex, Cursor and other clients can check a `.secondshift.yaml` before you
commit it, start approved jobs, submit tasks and read reports. Tokens are
scoped to a person and a workspace and can never approve access.

## What teams run

The [skill catalog](examples/README.md) has example skills to copy into your
repository and adapt.

| Team | Examples |
| --- | --- |
| Developers | [Repository health](examples/repository-health/README.md), [docs drift](examples/docs-drift/README.md), [implicit state machines](examples/implicit-state-machines/README.md), [CI failures](examples/ci-failure-review/README.md) |
| SRE and operations | [Alert triage](examples/alert-triage/README.md), [production errors](examples/production-error-review/README.md), [monitoring coverage](examples/monitoring-coverage-review/README.md), [on-call coverage](examples/on-call-coverage/README.md) |
| Security and compliance | [Security regression](examples/security-regression/README.md), [quarterly access review](examples/quarterly-access-review/README.md) |
| Engineering management | [Monday briefing](examples/engineering-briefing/README.md), [release readiness](examples/release-readiness-review/README.md), [ticket reconciliation](examples/ticket-reconciliation/README.md) |

## Run it in production

The [deployment guide](docs/production-deployment.md) covers a single-server
Docker Compose setup with HTTPS, PostgreSQL, S3 or local artifact storage, and
gVisor. It includes tested backup, restore and upgrade steps.

See [GitHub repository access](docs/github-app.md) to set up access with a
GitHub App or a personal access token.

## Security

| What | Where it lives during a run | Never reaches |
| --- | --- | --- |
| Model provider key | Gateway | Agent loop, sandbox, model |
| MCP and API credentials | Worker or gateway, per broker call | Sandbox, model |
| GitHub write token | Worker, after the sandbox stops | Sandbox, model |
| Subscription login | Runner, sealed to its key | Container arguments, environment, logs |

SecondShift treats repository content, model output and tool results as
untrusted.

Approving a write tool means trusting the agent with it. The
[security model](docs/security-model.md) covers this and the other risks that
remain.

## Status

SecondShift is in beta. See the [release notes](docs/releases/) for what
changed and what was tested.

## Learn more

- [Repository configuration](docs/repository-configuration.md)
- [Tasks](docs/tasks.md)
- [Agent subscriptions](docs/agent-subscriptions.md)
- [MCP server](docs/mcp.md) and [vendor MCP servers](docs/mcp-recipes.md)
- [Architecture](docs/architecture.md)
- [Alternatives](docs/comparisons.md)
- [Contributing](CONTRIBUTING.md)
