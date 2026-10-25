# Backend ownership

| App | Owns |
| --- | --- |
| `repositories` | Repository sources, revisions, checkout/materialization, sync triggers |
| `jobs` | Manifest interpretation, job reconciliation, capability requests, grants, execution-plan compilation, schedule registration |
| `runs` | Execution records, audit events, agent execution, durable workflows, recovery, artifacts, retention, report delivery |
| `connections` | Connection lifecycle, encrypted credentials, OAuth sign-ins, provider adapters for MCP, HTTP APIs, models and webhooks |
| `runner` | The isolated service's sandbox sessions, runtime enforcement, command execution, shared protocol and client |
| `gateway` | Model proxy, run tokens, model usage |
| `workspaces` | Membership, workspace selection, authorization and workspace settings endpoints |
| `core` | Account and application infrastructure, cross-domain dashboard overview |

Each HTTP app uses `api.py` for endpoints, `schemas.py` for its transport
contracts, and `serialization.py` for response builders when needed.
`config/api.py` composes the public routers. Domain modules do not import HTTP
schemas or route registration.

## Jobs and runs

Repository synchronization obtains configuration at a revision and calls
`jobs.sync`. The jobs app validates the manifest and reconciles definitions and
capability requests. Connections make external services available; job grants
authorize their use. `jobs.planning` freezes effective policy for a future run.
It does not import the agent execution framework.

Manual and scheduled triggers use `runs.services.create_run`. Manual execution
checks authorization and readiness first; scheduled creation retains its existing
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
client and the shared protocol; runtime flags, images, mounts and isolation
remain enforced by the runner service. No additional sandbox Django app exists.

## Migration compatibility

`automations` and `integrations` remain installed as migration-only apps. Do not
remove their migrations: existing installations and fresh databases both need
them. They contain no active models, APIs or services.

Generated `SeparateDatabaseAndState` migrations move model ownership to `jobs`,
`runs` and `connections`. Physical tables retain their legacy names, as do
existing constraints and indexes. Foreign key state in repositories and the
gateway follows the new owners without rewriting stored rows.

A reversible data migration updates existing content types in place. Permission
IDs, user/group assignments and admin log references keep their identity. A
collision with an already-existing destination content type fails explicitly.
Run the complete migration graph with `python manage.py migrate` during upgrade.

Public API URLs and payloads stay stable. Admin URLs and generated OpenAPI tags
and operation IDs reflect the new app ownership. DBOS workflow names, step names,
schedule names, workflow IDs and the durable agent name remain unchanged.
