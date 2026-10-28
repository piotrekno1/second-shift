# SecondShift, Grok Bot, Hermes Agent, and Viktor

[README](../README.md) · [Architecture](architecture.md) · [Security model](security-model.md)

| Product | Best for | How it runs |
| --- | --- | --- |
| **SecondShift** | Scheduled CI investigations, release checks, and repository reviews your team maintains in Git. | Open source. Run it locally or on your server, with your choice of model. |
| **[Grok Bot](https://docs.x.ai/grok-bot/overview)** | Research and tasks across apps and websites, with files and context kept between tasks. | Hosted service with a persistent computer and browser. |
| **[Hermes Agent](https://hermes-agent.nousresearch.com/docs)** | A general assistant you customize around your tools, habits, and preferred models. | Open source. Run it on your own machine or cloud infrastructure. |
| **[Viktor](https://viktor.com/)** | Delegating reports, follow-ups, and work across business tools from Slack or Teams. | Hosted service connected to your team's apps. |

## Why choose SecondShift?

For a task like “investigate CI failures every Monday,” SecondShift gives your
team a procedure it can review and operate across repositories:

- **Review changes in Git.** Commit the skill and schedule in
  `.secondshift.yaml`. Each run records its Git revision.
- **Approve access per job.** A workspace owner grants the requested tools.
  Merging a skill cannot grant credentials or expand its access.
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

Vendor sources checked October 28, 2026.
