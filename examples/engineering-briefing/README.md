# Monday engineering briefing

Summarize merged PRs, other default-branch commits, open work needing attention, and contributor participation
using live GitHub MCP data. Deliver the result through a webhook.

## Try it

1. SecondShift's root manifest already includes this job. Commit the changes,
   then import or resync your SecondShift checkout to discover it. For another
   repository, copy this directory's `.secondshift.yaml` and
   `.agents/skills/engineering-briefing` into its root and commit them. Merge the
   job into an existing manifest if needed. The example directory is a template,
   not an independent Git repository.
2. Connect a model provider and select its default model.
3. In **Connections**, choose **Add connection**, then **MCP server** and
   **GitHub**. Use capability name `github` and provide a fine-grained GitHub
   PAT that can read the repositories your jobs use. Classic tokens are refused.
   Under Repository permissions, grant read-only access to Pull requests,
   Commit statuses, and Contents. Check-run data may be unavailable with this
   token type. Your organization may
   need to approve the token. See [GitHub's token documentation](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).
4. Add a webhook with binding `engineering-reports`. A local HTTP receiver works
   in local mode. Outside local mode, use a public HTTPS destination.
5. Review and approve both capabilities for the job. Confirm that read access
   targets the job's GitHub repository. The MCP request should list
   `search_pull_requests`, `pull_request_read`, `list_commits`, and `get_commit`.
6. Run the job manually. Check the run report, MCP events, and webhook delivery
   before enabling its Monday schedule.

For a quieter repository, commit `briefing-window.json` beside the selected `SKILL.md`:

```json
{"start": "2026-10-01T00:00:00Z", "end": "2026-10-23T00:00:00Z"}
```

Remove the override and resync when finished testing. Live GitHub data can change
between calls. The Git commit pins the skill, not the state of GitHub.

For local checkouts, SecondShift derives the GitHub repository from the `origin`
remote during import or resync. HTTPS and SSH GitHub remotes are supported.
A checkout without a GitHub origin cannot use this capability.

## Supported boundary

This example uses GitHub's hosted read-only MCP endpoint over Streamable HTTP
with a fine-grained PAT. Connections can be reused across jobs. Each run
freezes the job's GitHub repository as its scope. The broker enforces that
scope and checks current grants before each call. Tokens remain outside command
sandboxes. Discovery proves the token can connect, not that it has every
permission needed to read the chosen repository.

Other services connect the same way. Sentry is built in. Custom MCP servers
sign in with OAuth or a bearer token, and HTTP APIs connect from an OpenAPI 3
spec. See [MCP and API declarations](../../docs/repository-configuration.md#mcp-and-api-declarations).
