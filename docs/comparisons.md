# Alternatives

[README](../README.md) · [Architecture](architecture.md) · [Security model](security-model.md)

| | Best for | Credentials the agent holds | Who approves access |
| --- | --- | --- | --- |
| **SecondShift** | [Team jobs reviewed in Git](repository-configuration.md) | [None. A broker calls tools for it](architecture.md#the-security-boundary) | [An owner, per job](security-model.md#who-grants-what), [down to arguments](security-model.md#tool-restrictions) |
| **[GitHub Actions](https://code.claude.com/docs/en/github-actions)** | A few jobs in one repository's CI | The job's secrets on the runner | Whoever merges the workflow |
| **[Harness Agents](https://developer.harness.io/harness-platform/use-harness-ai/core-capabilities/in-your-pipelines/worker-agent)** | AI steps in an existing Harness pipeline | [Secrets set as env vars](https://developer.harness.io/harness-ai/use-harness-ai/core-capabilities/in-your-pipelines/worker-agent/configuration), plus a [scoped Harness token](https://developer.harness.io/harness-ai/use-harness-ai/core-capabilities/in-your-pipelines/worker-agent/permissions) | Pipeline RBAC and OPA policies |
| **[OpenCode](https://opencode.ai/docs/github/)** | An open-source agent you run in CI or from cron | The runner's model key and GitHub token | Whoever merges the workflow |
| **[Claude Code scheduled tasks](https://code.claude.com/docs/en/routines)** | Your own recurring work | Acts as you | No one. A classifier checks calls |
| **[Grok Bot](https://docs.x.ai/grok-bot/overview)** | Browser work with persistent logins | Your logins on a hosted computer | Set up in the app |
| **[Hermes Agent](https://hermes-agent.nousresearch.com/docs)** | A personal assistant you host | Depends on the backend | Whoever hosts it |
| **[Viktor](https://viktor.com/)** | Work assigned from Slack or Teams | [Kept outside model context](https://viktor.com/security) | Access controls and approval gates |
| **[Executor](https://executor.sh/docs)** | One MCP endpoint for the agents you already run | [None. Apps attach credentials](https://executor.sh/docs/author-an-app) | App editors, plus per-tool approval policies |

OpenCode's [permissions](https://github.com/anomalyco/opencode/blob/dev/SECURITY.md)
are a prompt, not a sandbox.

Executor runs no agent. Its schedules run app code, and the agent calling its
tools runs unsandboxed wherever you run it.

An agent can still misuse a write tool an owner approved. See the
[remaining risks](security-model.md#remaining-risks). To try SecondShift, start
with [your first job](jobs.md#your-first-job).
