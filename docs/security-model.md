# Security model

[README](../README.md) · [Architecture](architecture.md) · [Self-hosting](self-hosting.md)

This page describes what a run can reach, where each secret lives, and the
risks that remain.

## Trust

| Party | Trusted | Why |
| --- | --- | --- |
| Server operator | Yes | Runs the servers, sets the sandbox image, runtime and limits. |
| Workspace owner | Yes | Connects services, chooses tools, approves jobs. |
| Workspace admin | Partly | Runs and schedules approved jobs. Cannot add connections or approve access. |
| Repository content | No | Anyone who can merge to the repository controls it. |
| Model output | No | Repository content and tool results can steer it. |
| Tool results | No | They come from outside services. |

A merged change to `.secondshift.yaml` can only request access. It cannot grant
access, choose a credential or endpoint, or change the sandbox.

## What a run can reach

A run can use three things. Nothing else is reachable from it.

1. **The sandbox.** Commands run in a container started with `--network=none`,
   a read-only root filesystem, all Linux capabilities dropped, an unprivileged
   user, and limits on memory, CPU and processes. It holds a copy of the
   repository at the run's commit and no credentials. Production requires the
   gVisor runtime (`runsc`).
2. **The model gateway.** The agent loop reaches the model provider only
   through the gateway. A run authenticates with its own token, which only
   works while that run is active. The gateway forwards only a fixed list of
   provider paths and cuts the run off once its token budget is used.
3. **The tool broker.** Each MCP or API call is checked against the run's frozen
   execution plan, the job's grant, and the connection's allowed tools.
   Arguments must match the tool's schema. Calls leave from the worker, never
   from the sandbox, and reach only public HTTPS addresses in production.

Webhook reports are sent after the run by the worker. The model does not choose
the destination or call the webhook.

## Where secrets live

All credentials are encrypted in the database with `FIELD_ENCRYPTION_KEY`.

| Secret | During a run, decrypted by | Never reaches |
| --- | --- | --- |
| Model provider key | The gateway service | The agent loop, the sandbox, the model |
| MCP and API credentials | The worker, inside the broker call | The sandbox, the model, audit events |
| OAuth refresh tokens | The worker, when a token expires | The sandbox, the model |
| Webhook URL | The worker, when delivering | The sandbox, the model |
| Gateway run token | Held by the agent loop for one run | The sandbox. Only its hash is stored |

The agent loop and the broker run in the same worker process. The model never
receives MCP or API credentials, but no process boundary separates them from
the agent loop. The web app, gateway and worker also share the encryption key,
so a compromise of any of them exposes every stored credential. The runner and
the sandbox do not have the key.

Audit events record the tool name and the connection, never the arguments,
credentials, or responses.

## Who grants what

A tool becomes available to a run only when all of these hold:

- the job's committed configuration requests it,
- the owner allowed it on the connection,
- the owner approved the job's request,
- it was in the execution plan when the run started.

Revoking a grant or a connection takes effect at the next tool call, and the
run fails. A run that requests a missing required grant fails before it starts.
Requesting more tools in the repository needs a new approval.

GitHub and Sentry offer only read tools that SecondShift allowlists, limited to
the job's repository or the chosen Sentry organization. For custom MCP servers
and HTTP APIs, the owner's tool choice is the limit. The access review marks
tools that may change data.

## Remaining risks

- **Approved tools can be misused.** A prompt injection in repository content or
  a tool result can make the model call any approved tool with any arguments
  its schema allows. An approved tool that changes data can be used to change
  data the job was not meant to touch.
- **Data can leave through approved tools.** The model can put repository
  content into an approved call, for example a search query, and send it to
  that service.
- **The model provider sees the context.** Repository content sent to the model
  leaves your environment unless you use a local provider.
- **The credential sets the reach.** For custom MCP servers and HTTP APIs,
  SecondShift cannot narrow what the credential itself can access. Use the
  narrowest token the service offers.

Approve only the tools a job needs, and prefer read-only credentials.

## Adding a connection type

A new kind of connection must:

- keep its credential outside the sandbox and out of the model's context,
- make every call through the broker, checked against the frozen plan,
- let the owner choose which tools jobs may use,
- refuse endpoints, credentials or scopes from repository configuration,
- send outbound requests through the shared address check.
