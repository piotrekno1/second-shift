# How SecondShift compares

[README](../README.md) · [Architecture](architecture.md) · [Security model](security-model.md)

| Product | Best for | How it runs |
| --- | --- | --- |
| **SecondShift** | Making recurring tasks part of your company's operating model, with reviewed procedures, role-based governance, approved access, shared run history, and clear token usage and costs. | Open source. Run it locally or on your server, with your choice of model. |
| **[GitHub Actions](https://code.claude.com/docs/en/github-actions)** | A few agent jobs in a repository that already runs its CI on GitHub. | A scheduled workflow runs an agent action, such as `claude-code-action`, on a GitHub runner. |
| **[Claude Code scheduled tasks](https://code.claude.com/docs/en/routines)** | Personal recurring work for one developer, with no infrastructure to run. | Hosted by Anthropic on your Claude plan. Formerly called routines. In research preview. |
| **[Grok Bot](https://docs.x.ai/grok-bot/overview)** | Research and tasks across apps and websites, with files and context kept between tasks. | Hosted service with a persistent computer and browser. |
| **[Hermes Agent](https://hermes-agent.nousresearch.com/docs)** | A general assistant you customize around your tools, habits, and preferred models. | Open source. Run it on your own machine or cloud infrastructure. |
| **[Viktor](https://viktor.com/)** | Delegating reports, follow-ups, and work across business tools from Slack or Teams. | Hosted service connected to your team's apps. |

## Why choose SecondShift?

For a task like “investigate CI failures every Monday,” SecondShift gives your
team a procedure it can review and operate across repositories:

- **Review changes in Git.** Commit the skill and schedule in
  `.secondshift.yaml`. Each run records its Git revision.
- **Approve access per job.** A workspace owner grants the requested tools.
  An owner can also let a connection skip approval. Merging a job then grants
  the tools that connection already allows. Merging never grants credentials
  or tools the owner has not allowed.
- **Share ownership and history.** Owners grant access, admins operate jobs,
  and members view reports, tool activity, token usage, and model cost.
- **Inspect and run it yourself.** SecondShift is open source under
  [Apache 2.0](../LICENSE). Review how permissions and sandbox isolation work,
  self-host it, and configure models, connections, and runtime limits.

Enable [`history`](repository-configuration.md#run-history) to give jobs earlier
successful reports as context.

Each run gets a fresh sandbox without network access or service credentials.
Sandbox files and sessions do not persist between runs.
MCP and HTTP API calls go through a broker that checks the job's grants.
Built-in GitHub and Sentry tools are read-only. Custom connections can expose
write operations.

An agent can misuse an approved write tool. Job grants do not require human
review of each action. See the [security model](security-model.md).

## Why choose GitHub Actions?

Choose GitHub Actions if you already run CI on GitHub and need one or two
agent jobs. A workflow with a `schedule` trigger runs
[`claude-code-action`](https://github.com/anthropics/claude-code-action) on a
cron. The prompt and allowed tools live in the workflow file, so changes are
reviewed in Git. It works with an API key, Amazon Bedrock, Google Vertex AI,
Microsoft Foundry, or a Claude subscription token. See
[Claude Code GitHub Actions](https://code.claude.com/docs/en/github-actions).

The agent runs on the runner that holds the job's secrets. That includes the
model key and any tokens its tools need. GitHub-hosted runners have outbound
network access. Tool limits are allowlists passed as CLI flags. The action's
own [security notes](https://github.com/anthropics/claude-code-action/blob/main/docs/security.md)
call its default auto mode "a review, not a boundary." Each repository sets up
its own workflow. Run history, token budgets, and delivery to Slack are yours
to build.

Choose SecondShift when the agent must not hold credentials, or when many
jobs need approved tool access, budgets, and shared reports across
repositories.

## Why choose Claude Code scheduled tasks?

Choose Claude Code scheduled tasks if you want your own recurring work to run
without hosting anything. You save a prompt, repositories, and connectors in
your claude.ai account. Tasks run on a schedule, from an API call, or on GitHub
events. They can use skills committed to the repository. Runs count against
your Claude plan. See [Scheduled tasks](https://code.claude.com/docs/en/routines).

A task belongs to one account and is not shared with teammates. Its
configuration lives in that account, not in the repository. Commits, pull
requests, and connector actions use your own GitHub user and linked accounts.
Network access follows the environment's policy, which is limited by default.
A classifier decides which tool calls to allow. There is no per-job grant an
owner approves. Runs use Claude models only.

Choose SecondShift when a job belongs to the team, its procedure and schedule
are reviewed in Git, an owner approves its access, or you need another model
or your own servers.

## Why choose Grok Bot?

Choose Grok Bot for work that needs a browser, persistent files and logins,
or back-and-forth discussion. It keeps context between tasks and lets Bots
coordinate. See the [overview](https://docs.x.ai/grok-bot/overview).

It already has [scheduled and event-triggered routines](https://docs.x.ai/grok-bot/skills-routines-and-automations)
and [shared Bots](https://docs.x.ai/grok-bot/shared-bots). Choose SecondShift when you need the procedure
reviewed in Git and each job's access approved separately.

## Why choose Hermes Agent?

Choose Hermes if you want to host and customize a general assistant around
your tools and habits. It offers persistent memory, reusable skills, scheduled
work, messaging integrations, model choice, and several execution backends.
See the [Hermes documentation](https://hermes-agent.nousresearch.com/docs).

Both Hermes and SecondShift are open source and support self-hosting and
scheduled skills. SecondShift adds a workspace for managing repository jobs,
access grants, and run history.

## Why choose Viktor?

Choose Viktor if your team wants to assign work from Slack or Teams across
its business tools. It remembers processes and can repeat tasks on a schedule.
See [Viktor](https://viktor.com/).

Viktor also has integration access controls and approval gates for sensitive
actions. Its gateway keeps credentials outside model context.
See [Viktor security](https://viktor.com/security).

Choose SecondShift when the task belongs in a repository, with changes
reviewed by maintainers and runs managed by the engineering team.

Vendor sources checked October 28, 2026. GitHub Actions and Claude Code sources
checked November 13, 2026.
