# SecondShift, Grok Bot, Hermes Agent, and Viktor

[README](../README.md) · [Architecture](architecture.md) · [Security model](security-model.md)

| Choose | When you want | What you take on |
| --- | --- | --- |
| **SecondShift** | Recurring repository jobs with Git review, separate access approvals, and shared run history. | Hosting, connection setup, and the limits of networkless sandboxes. Currently beta. |
| **[Grok Bot](https://docs.x.ai/grok-bot/overview)** | A hosted assistant with a persistent computer, browser sessions, and memory. | Managing persistent files and logins within a hosted service. |
| **[Hermes Agent](https://hermes-agent.nousresearch.com/docs)** | An agent you can host and customize, with memory, skills, and messaging integrations. | Configuring and maintaining its deployment, tools, and execution environment. |
| **[Viktor](https://viktor.com/)** | An assistant in Slack or Teams that works across business tools. | Setting up connections and managing workflows in a hosted service. |

## Why choose SecondShift?

For a task like “investigate CI failures every Monday,” SecondShift gives your
team a procedure it can review and operate across repositories:

- **Review changes in Git.** Commit the skill and schedule in
  `.secondshift.yaml`. Each run records its Git revision.
- **Approve access per job.** A workspace owner grants the requested tools.
  Merging a skill cannot grant credentials or expand its access.
- **Share ownership and history.** Owners grant access, admins operate jobs,
  and members view reports, tool activity, token usage, and model cost.
- **Choose your host and model.** Run SecondShift with your own model provider,
  including a local provider.

Enable [`history`](repository-configuration.md#run-history) to give jobs earlier
successful reports as context.

Each run gets a fresh sandbox without network access or service credentials.
Sandbox files and sessions do not persist between runs.
MCP and HTTP API calls go through a broker that checks the job's grants.
Built-in GitHub and Sentry tools are read-only. Custom connections can expose
write operations.

An agent can misuse an approved write tool. Job grants do not require human
review of each action. See the [security model](security-model.md).

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

Both Hermes and SecondShift support self-hosting and scheduled skills.
SecondShift adds a workspace for managing repository jobs, access grants,
and run history.

## Why choose Viktor?

Choose Viktor if your team wants to assign work from Slack or Teams across
its business tools. It remembers processes and can repeat tasks on a schedule.
See [Viktor](https://viktor.com/).

Viktor also has integration access controls and approval gates for sensitive
actions. Its gateway keeps credentials outside model context.
See [Viktor security](https://viktor.com/security).

Choose SecondShift when the task belongs in a repository, with changes
reviewed by maintainers and runs managed by the engineering team.

Vendor sources checked October 28, 2026.
