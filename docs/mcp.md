# MCP server

[README](../README.md) · [Security model](security-model.md) · [Repository configuration](repository-configuration.md)

> **Status: planned.** This page is the design spec. Nothing here is built yet.

SecondShift exposes an MCP server for coding agents and other integrations.
An agent can read jobs, runs and reports, start approved jobs, and check a
`.secondshift.yaml` before it is committed. An agent can never grant access.
Approvals, connections and secrets stay in the dashboard.

## Connect a client

1. Open the account menu and choose **API tokens**.
2. Create a token. Pick the workspace it works in, a scope and an expiry.
3. Copy the setup command. The token is shown once.

Claude Code:

```bash
claude mcp add --transport http secondshift-acme https://secondshift.example.com/mcp \
  --header "Authorization: Bearer ss_..."
```

The server name ends with the workspace's slug. Someone in several
workspaces adds one server per workspace, and the names do not clash.

Other clients use the same URL and header. The server speaks Streamable HTTP.

In local mode, `secondshift mcp-token` creates a token for the owner and
prints the `claude mcp add` command with it. The server is at
`http://127.0.0.1:8000/mcp` while `secondshift up` runs. There is no stdio
transport. Every client uses a token, so the same scopes and expiry apply
everywhere.

## API tokens

A token belongs to one person and works in one workspace. People manage
their own tokens from their account, across every workspace they belong to.
A token has one of two scopes.

| Scope | Allows | Creator needs at least |
| --- | --- | --- |
| `read` | Every read and authoring tool | Member |
| `run` | Everything in `read`, plus starting runs and resyncing repositories | Admin |

The `run` scope can start runs. Runs spend model budget. A run may change
data in connected services through tools the owner approved. The token form
says so. It offers `run` only for workspaces where the person is an admin or
owner. With one workspace, the form shows its name instead of a picker.

A token covers one workspace so that content from one workspace cannot steer
an agent into another. A prompt injection in one workspace's report cannot
reach a second workspace's reports or jobs. A leaked token exposes one
workspace.

Rules:

- Access is the token's scope limited by the creator's current role. It is
  checked on every request. A demoted creator's `run` token can only read. A
  creator who leaves the workspace loses every token.
- Owner actions are never available to a token. These include grants,
  approvals, connections, secrets, model providers, limits and members.
- Only a hash of the token is stored. Tokens start with `ss_` so secret
  scanners can find leaked ones.
- Every token expires. The creator picks 7, 30 or 90 days or a custom date.
  The choice is capped by the server's `SECONDSHIFT_API_TOKEN_MAX_DAYS` (90 by
  default), which is also the default choice. There is no option for a token
  that never expires.
- Lowering the maximum affects only new tokens. Existing tokens keep their
  expiry.
- A person can revoke their own tokens. An owner limits other people's
  tokens through membership. Removing someone stops their tokens at once.
  Demoting them to member makes their `run` tokens read only.
- The token list shows the name, workspace, scope, creation time, expiry and
  last use.

## Tools

All tools act on the token's workspace. IDs are the same as in the dashboard
and the HTTP API.

### Jobs and runs

| Tool | Scope | Arguments | Returns |
| --- | --- | --- | --- |
| `list_jobs` | `read` | `repository_id?`, `enabled?` | Each job's ID, key, name, repository, schedule, whether it is enabled, and whether it is ready to run. A job that is not ready lists what blocks it. |
| `get_job` | `read` | `job_id` | Skill path, requested capabilities and their grant status, limits and recent runs. |
| `list_runs` | `read` | `job_id?`, `repository_id?`, `status?`, `period?`, `page?`, `page_size?` | A page of runs with status, times and token usage. Page size is at most 50. |
| `get_run` | `read` | `run_id` | Status, failure reason, tool call events and model usage. |
| `get_run_report` | `read` | `run_id` | The run's Markdown report. A large report is cut at a server limit and marked as cut. |
| `run_job` | `run` | `job_id` | The new run's ID and status. Poll `get_run` to follow it. |
| `sync_repository` | `run` | `repository_id` | The synced commit and the jobs it added, changed or removed. A broken manifest returns its errors. |

`run_job` follows the same rules as **Run now**. It fails with a plain message
when the repository is disabled, no default model is connected, or a required
capability is not approved.

`run_job` also refuses while the job already has a queued or running run. It
returns that run's ID instead, so the agent can follow it with `get_run`. An
agent stuck in a loop cannot queue runs faster than they finish. **Run now**
in the dashboard follows the same rule and links to the active run.

## Rate limit

Each person can make `SECONDSHIFT_MCP_RATE_LIMIT` tool calls per minute (300
by default). All of their tokens share the limit, so a new token does not
reset it. Only tool calls count. Starting a session and listing tools do not.
The dashboard's own API is not limited by it. A call over the limit fails with a plain message and the number of
seconds to wait.

Runs are already bounded by their own limits, the worker queue and the
workspace token budget. The rate limit only stops runaway clients.

### Writing manifests

| Tool | Scope | Arguments | Returns |
| --- | --- | --- | --- |
| `list_bindings` | `read` | none | Each binding in the workspace with its kind, whether it is connected, and the tools a job may request. Each tool has a name, description, input schema and a flag for tools that may change data. Built-in GitHub and Sentry tools are listed even when they are not connected. |
| `check_manifest` | `read` | `yaml` | Whether the manifest is valid, with the same error messages a sync shows. For a valid manifest, each job's bindings with their status in this workspace: `ready`, `missing`, `needs_approval` or `unavailable`. |
| `list_capability_needs` | `read` | none | What every job in the workspace has requested and what is still missing. |

`list_bindings` shows only tools the owner allowed on the connection. It
reports how many other tools exist without naming them. A job that requests a
tool the owner has not allowed cannot run, so listing it would only create
approval requests.

`check_manifest` does not read the repository. It checks that skill paths
are well formed but cannot check that the files exist. A sync reads them from
the committed revision.

## What is not exposed

| Left out | Why |
| --- | --- |
| Grants and approvals | An agent driven by repository content must not approve its own access. |
| Enabling a job's schedule | Letting a job run unattended is a human decision. |
| Connections, secrets and model providers | Owner only. Credentials never leave the server. |
| Endpoints, server URLs, auth types and OAuth details | A manifest may not contain them. An agent that never sees them cannot copy them into one. |
| Limits, members and invites | Owner or admin settings with no use in an agent loop. |
| Local repository paths | They describe the host's file system. |

## Untrusted content

Reports, run events and tool descriptions are untrusted. Reports are model
output built from repository content. Tool names, descriptions and schemas
of custom MCP servers and HTTP APIs come from third parties. Any of them can
contain instructions aimed at the agent that reads them.

Tool results mark this content as untrusted. Tool descriptions are cut at a
fixed length. A client should treat a report as data to act on, not as
instructions.

## Audit

Each tool call records an audit event with the tool name, the token, its
creator and the result status. Arguments and results are not recorded.

## Implementation notes

For contributors. This section is removed when the server ships.

- The server lives in `secondshift/mcp_server/`. The name avoids a clash with
  the `mcp` SDK and with `connections/mcp/`.
- It uses [thin-mcp](https://github.com/wild-edge/thin-mcp), pinned to
  `thin-mcp[django]>=0.2,<0.3`. The `McpAPI` Ninja adapter is mounted at
  `/mcp`. Tool arguments and results reuse the app `schemas.py` models where
  they fit.
- Tokens live in `workspaces`. A Ninja `HttpBearer` class resolves a token to
  its creator and workspace, so `require()` works unchanged.
- Each tool is a thin adapter in `mcp_server/tools/<domain>.py` that calls the
  same service as the matching HTTP view. Logic that lives in a view today,
  such as the readiness checks in `run_job`, moves to its app's services
  first.
- `check_manifest` needs `jobs.needs.capability_needs` to accept a parsed
  draft manifest in addition to the synced jobs.

Planned pull requests:

1. API tokens (#99): model, bearer auth, account endpoints and
   `SECONDSHIFT_API_TOKEN_MAX_DAYS`. The dashboard account page with the
   workspace picker and expiry choice follows as its own pull request.
2. Server skeleton: thin-mcp, the `/mcp` mount, token auth, audit and the
   rate limit. The rate limit counts calls in the shared cache from #98.
3. Service moves out of views. The one behavior change: **Run now** refuses
   while the job has a queued or running run.
4. Job and run tools.
5. Manifest tools.
6. This page becomes the user guide. README link, client examples and the
   `secondshift mcp-token` command.
