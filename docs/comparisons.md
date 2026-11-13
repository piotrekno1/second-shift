# Alternatives

[README](../README.md) · [Architecture](architecture.md) · [Security model](security-model.md)

| | Best for | Credentials the agent holds | Who approves access |
| --- | --- | --- | --- |
| **SecondShift** | [Team jobs reviewed in Git](repository-configuration.md) | [None. A broker calls tools for it](architecture.md#the-security-boundary) | [An owner, per job](security-model.md#who-grants-what), [down to arguments](security-model.md#tool-restrictions) |
| **[GitHub Actions](https://code.claude.com/docs/en/github-actions)** | A few jobs in one repository's CI | The job's secrets on the runner | Whoever merges the workflow |
| **[Claude Code scheduled tasks](https://code.claude.com/docs/en/routines)** | Your own recurring work | Acts as you | No one. A classifier checks calls |
| **[Grok Bot](https://docs.x.ai/grok-bot/overview)** | Browser work with persistent logins | Your logins on a hosted computer | Set up in the app |
| **[Hermes Agent](https://hermes-agent.nousresearch.com/docs)** | A personal assistant you host | Depends on the backend | Whoever hosts it |
| **[Viktor](https://viktor.com/)** | Work assigned from Slack or Teams | [Kept outside model context](https://viktor.com/security) | Access controls and approval gates |

An agent can still misuse a write tool an owner approved. See the
[remaining risks](security-model.md#remaining-risks). To try SecondShift, start
with [your first job](repository-configuration.md#your-first-job).
