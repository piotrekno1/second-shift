# Vendor MCP servers

[README](../README.md) · [Repository configuration](repository-configuration.md) · [Security model](security-model.md)

Some vendors run an official MCP server. Connect one as a **Custom server**
under **Connections** → **Add connection**. SecondShift has no built-in
support for these vendors. Any MCP server that offers sign-in or a bearer
token connects the same way.

## Connect any MCP server

1. Find the vendor's MCP server URL. It usually ends in `/mcp`. Use a
   read-only URL if the vendor offers one.
2. Choose **Sign in** if the server offers it. Tick only the scopes the job
   needs, and prefer read scopes. SecondShift refuses the sign-in if the
   server grants more than you ticked. Without sign-in, use the narrowest
   bearer token the vendor offers.
3. Sign in as an account that can see only what jobs should see. The
   credential sets the reach. Unselected tools do not limit it.
4. Allow only the tools jobs need. Tools the server marks read-only start
   selected. **May change data** means the server did not mark the tool
   read-only. Read the tool's description before allowing it.
5. Add a `requires` block with the binding and the tools. Run the job once by
   hand and read the run's timeline before scheduling it.

## Example: Linear

Checked on 2026-11-01. The tools and `requires` block below are a sample for
a job that reads issues and projects. Pick the tools your own job needs.

| Setting | Value |
| --- | --- |
| Binding | `linear` |
| Server URL | `https://mcp.linear.app/mcp/readonly` |
| Sign-in | **Sign in**, with the `read` scope ticked |

Linear's `/mcp/readonly` endpoint offers only read tools, and the `read`
scope keeps the token away from Linear's write APIs. Use the plain `/mcp`
endpoint only for a job that must create or change issues.

Linear marks none of its tools read-only. No tool starts selected, and every
tool shows **May change data**. On this endpoint with the `read` scope, none
of them can change data.

This example allows these tools on the connection:

| Tool | Returns |
| --- | --- |
| `list_teams` | Teams in the workspace |
| `list_issues` | Issues, filtered by team, state, assignee, label, project or date |
| `get_issue` | One issue with its attachments and branch name |
| `list_comments` | Comments on an issue, project or document |
| `list_projects` | Projects in the workspace |
| `get_project` | One project, with its milestones and members on request |

```yaml
requires:
  mcps:
    - binding: linear
      tools:
        - list_teams
        - list_issues
        - get_issue
        - list_comments
        - list_projects
        - get_project
```

`list_issues` skips archived issues unless the job passes `includeArchived`.

**What the credential reaches.** The token acts as the person who signed in,
in the Linear workspace they signed in to. It can read everything that
person can see there, including private teams they belong to. Other tools on
this endpoint read that person's notifications (`get_notifications`), list
members with their email addresses (`list_users`, `get_user`), and fetch
images from issue text (`extract_images`). Allow them only for a job that
needs them.

## Other vendors

On 2026-11-01, SecondShift found sign-in settings on these servers. Nobody
completed a sign-in or ran a job against them. Follow the steps above and
check the tool list after connecting.

| Vendor | Server URL | Sign-in | Limiting access |
| --- | --- | --- | --- |
| Atlassian | `https://mcp.atlassian.com/v2/mcp?tools=all` | Offered | Tick read scopes only, such as `read:jira:agent-interface` and `read:confluence:agent-interface`. Keep `?tools=all`. Without it, the server offers generic discover and execute tools instead of named ones. |
| Datadog | `https://mcp.datadoghq.com/v1/mcp`. Other sites use their own host, such as `mcp.datadoghq.eu`. | Offered, with one scope, `mcp_all`. Datadog's docs also allow a personal or service access token as a bearer token. | Pick tools with `?toolsets=`. Only `core` is on by default. Users without the `mcp_write` permission get no write tools. |
| Notion | `https://mcp.notion.com/mcp` | Offered, with one scope, `default` | No read-only option is documented. The token reaches everything the person who signed in can see. |

SecondShift cannot sign in to PagerDuty's server
(`https://mcp.pagerduty.com/mcp`), because it does not let new apps register.
PagerDuty's docs send API tokens as `Token token=...`, and SecondShift sends
tokens as `Bearer`, so the token option was not tried.
