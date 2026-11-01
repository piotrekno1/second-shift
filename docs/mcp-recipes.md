# Vendor MCP servers

[README](../README.md) · [Repository configuration](repository-configuration.md) · [Security model](security-model.md)

Some vendors run an official MCP server. Connect one as a **Custom server**
under **Connections** → **Add connection**. SecondShift has no built-in
support for these vendors. Any MCP server that offers sign-in or a bearer
token connects the same way.

The recipes below are examples. Each gives the server URL, how to sign in,
the tools to allow, and a `requires` block for a job. Pick the tools your own
job needs. The examples use read-only tools.

Each example was checked against the vendor's server on the date it gives.
Vendors add and rename tools, so compare the list with what the connection
shows after you sign in.

## Example: Linear

Checked on 2026-11-01. The tools and `requires` block below are a sample for
a job that reads issues and projects.

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
person can see there, including private teams they belong to. Leaving tools
unselected limits what jobs can call, not what the token can read. Other tools on this endpoint read that person's notifications
(`get_notifications`), list members with their email addresses
(`list_users`, `get_user`), and fetch images from issue text
(`extract_images`). Allow them only for a job that needs them.

To limit the reach, sign in as a Linear user who belongs only to the teams
the job should see.
