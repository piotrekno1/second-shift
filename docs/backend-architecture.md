# Backend ownership

| App | Owns |
| --- | --- |
| `repositories` | Repository sources, revisions, mirrors and run snapshots, sync triggers |
| `jobs` | Manifest interpretation, job reconciliation, capability requests, grants, execution-plan compilation, schedule registration |
| `runs` | Execution records, audit events, agent execution, durable workflows, recovery, artifacts, retention, report delivery |
| `connections` | Connection lifecycle, encrypted credentials, OAuth sign-ins, provider adapters for MCP, HTTP APIs, models and webhooks |
| `runner` | The isolated service's sandbox sessions, runtime enforcement, command execution, shared protocol and client |
| `gateway` | Model proxy, run tokens, model usage |
| `mcp_server` | The `/mcp` endpoint, MCP tool adapters, tool call audit and rate limit |
| `workspaces` | Membership, workspace selection, authorization, API tokens and workspace settings endpoints |
| `core` | Account and application infrastructure, cross-domain dashboard overview |

Each HTTP app uses `api.py` for endpoints, `schemas.py` for its transport
contracts, and `serialization.py` for response builders when needed.
`config/api.py` composes the public routers. Domain modules do not import HTTP
schemas or route registration.

## Jobs and runs

Repository synchronization obtains configuration at a revision and calls
`jobs.sync`. The jobs app validates the manifest and reconciles definitions and
capability requests. Connections make external services available. Job grants
authorize their use. `jobs.planning` freezes effective policy for a future run.
It does not import the agent execution framework.

Manual and scheduled triggers use `runs.services.create_run`. Manual execution
checks authorization and readiness first. Scheduled creation retains its existing
idempotency and execution-time readiness checks. Existing execution plans and
input snapshots remain attached to their run. Runtime checks still enforce
connection ownership and revocation.

The runs app has three internal packages:

- `execution`: agent construction, workspace and external tools, budget and
  deadline enforcement, model gateway access, tool authorization at call time.
- `orchestration`: DBOS registration, dispatch, workflows, reconciliation and
  periodic worker tasks.
- `reporting`: history queries, failure summaries, health, connection usage and
  report delivery payloads.

`runner.client` is the public client for sandbox operations. Runs consume that
client and the shared protocol. Runtime flags, images, mounts and isolation
remain enforced by the runner service. No additional sandbox Django app exists.

## Database migrations

First-party migration history starts from the current app structure. Each
model-owning app has generated initial migrations, and tables use their owning
app's name. The obsolete `automations` and `integrations` apps have been removed.
The control plane and runner have independent migration graphs and databases.

This alpha reset does not support upgrading a database created with the previous
migration history. Back up existing data before switching versions, then create
fresh control-plane and runner databases, including fresh DBOS workflow state.
Do not fake the new initial migrations against an old database: its tables and
content types belong to the previous app structure. Connections, approvals and
schedules must be configured again.

For a new control-plane database, run `python manage.py migrate`. For a new
runner database, use the runner's environment and settings:

```bash
python manage.py migrate --settings=secondshift.config.settings.runner
```

Public API URLs and payloads stay stable. Admin URLs and generated OpenAPI tags
and operation IDs reflect the new app ownership. DBOS workflow names, step names,
schedule names, workflow IDs and the durable agent name remain unchanged.
