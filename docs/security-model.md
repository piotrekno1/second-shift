# Security model

[README](../README.md) · [Architecture](architecture.md) · [Self-hosting](self-hosting.md)

This page describes what a run can reach, where each secret lives, and the
risks that remain.

## Trust

| Party | Trusted | Why |
| --- | --- | --- |
| Server operator | Yes | Runs the servers, sets the sandbox image, runtime and limits. |
| Workspace owner | Yes | Connects services, chooses tools, approves jobs, decides who joins. |
| Workspace admin | Partly | Runs and schedules approved jobs. Cannot add connections or approve access. |
| Repository content | No | Anyone who can merge to the repository controls it. |
| Model output | No | Repository content and tool results can steer it. |
| Tool results | No | They come from outside services. |

A merged change to `.secondshift.yaml` can request access. It cannot choose a
credential or endpoint, use a tool the owner did not allow, loosen a tool
restriction, or change the sandbox.

Whether a request also needs an owner's approval is set on each connection with
**Require approval for each job**. It is off for new connections. Then merge
rights on a connected repository are the gate: anyone who can merge can give a
job any of the connection's allowed tools. Connections created before this
setting existed keep requiring approval.

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
   Arguments must match the tool's schema and the owner's
   [tool restrictions](#tool-restrictions). Calls leave from the worker, never
   from the sandbox, and reach only public HTTPS addresses in production.

Webhook reports are sent after the run by the worker. The model does not choose
the destination or call the webhook.

## Where secrets live

All credentials are encrypted in the database with `SECONDSHIFT_ENCRYPTION_KEY`.

| Secret | During a run, decrypted by | Never reaches |
| --- | --- | --- |
| Model provider key | The gateway service | The agent loop, the sandbox, the model |
| MCP and API credentials | The worker, inside the broker call | The sandbox, the model, audit events |
| OAuth refresh tokens | The worker, when a token expires | The sandbox, the model |
| Webhook URL | The worker, when delivering | The sandbox, the model |
| Webhook trigger secret | The web app, to check a request | The sandbox, the model. Shown once |
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
- the owner approved the job's request, or the connection does not require
  approval,
- it was in the execution plan when the run started.

Without approval, a request is granted at sync when every tool it asks for is
allowed on the connection. A request for any other tool waits for an owner.
Turning approval back on keeps existing grants, and new or changed requests wait
again. An owner's denial is never overridden.

Teams that turn approval off and want a specific reviewer can require one with
a `CODEOWNERS` entry for `.secondshift.yaml`:

```text
/.secondshift.yaml @your-org/platform-owners
```

Revoking a grant or a connection takes effect at the next tool call, and the
run fails. A run that requests a missing required grant fails before it starts.
Requesting more tools in the repository needs a new approval when the
connection requires one.

GitHub and Sentry offer only read tools that SecondShift allowlists, limited to
the job's repository or the chosen Sentry organization. For custom MCP servers
and HTTP APIs, the owner's tool choice and [tool restrictions](#tool-restrictions)
are the limit. The access review marks tools that may change data.

## Securing MCP and API calls

Every MCP or API call passes these layers, from the widest to the narrowest.
The owner or SecondShift sets each one. The repository sets none of them.

| Layer | Set by | What it limits |
| --- | --- | --- |
| Credential | Owner, when connecting | Everything the service lets the token do |
| Allowed tools | Owner, on the connection | Which tools any job may use |
| Job approval | Owner, when the connection requires it | Which of those tools this job gets |
| Tool restrictions | Owner, in the job's access review | Which argument values this job may send |
| Built-in scoping | SecondShift, for GitHub and Sentry | The job's repository, or the chosen Sentry organization |
| Broker checks | SecondShift, on every call | The run's frozen plan, the grant, and the tool's schema |

To secure a job that uses a tool that changes data:

1. Use the narrowest credential the service offers.
2. Allow only the tools jobs need on the connection.
3. Turn on **Require approval for each job** if merge rights alone should not
   grant access.
4. Restrict the arguments that pick a target, such as a channel, project or
   recipient.
5. Run the job manually and read the run's timeline before scheduling it.

### Tool restrictions

An owner can narrow how one job calls an approved MCP tool or API operation. In
the job's access review, choose **Add restriction** under the tool, then pick an
argument and a rule.

| Rule | Effect |
| --- | --- |
| Always this value | The broker always sends this value. The model does not see the argument. |
| Only these values | The model must send one of these values. |
| At most | The model must send a number no larger than this. |
| Never sent | The model may not send the argument. Not offered for arguments the tool requires. |

For example, a digest job that may use Slack's `post_message` can be limited to
the `#alerts` and `#ops` channels.

- Restrictions belong to one job's grant. Other jobs on the same connection are
  not affected.
- They apply to top-level arguments. For an HTTP API, those are the path and
  query parameters, and the request body as a whole.
- They are copied into each run's plan when the run starts. A change applies
  from the next run.
- The model sees the tool's schema with the restrictions applied. The broker
  enforces them. A call that breaks one never reaches the service, and the run
  fails with a **Tool restriction** error. The error names the argument, never
  the value the model sent.
- Built-in scoping still applies after restrictions, so a restriction cannot
  widen it.
- If the service changes a tool's schema and a restriction no longer fits, the
  job is blocked until the owner updates the restriction.
- `.secondshift.yaml` cannot set or change restrictions.

## Remaining risks

- **Approved tools can be misused.** A prompt injection in repository content or
  a tool result can make the model call any approved tool with any arguments
  its schema and restrictions allow. An approved tool that changes data can be used to change
  data the job was not meant to touch.
- **Tool results can carry injected text.** Pull request text and CI job logs
  can be written by anyone who can open a pull request. GitHub masks the
  secrets it knows about in logs, but not values it was never told are secret.
- **Data can leave through approved tools.** The model can put repository
  content into an approved call, for example a search query, and send it to
  that service.
- **The model provider sees the context.** Repository content sent to the model
  leaves your environment unless you use a local provider.
- **The credential sets the reach.** For custom MCP servers and HTTP APIs,
  SecondShift cannot narrow what the credential itself can access. Use the
  narrowest token the service offers.

- **A webhook trigger starts runs for whoever holds its secret.** Each run uses
  tokens and the job's approved tools. Turn a trigger off or rotate its secret
  if it may have leaked. A request signed without a timestamp header can be
  sent again later, so set the timestamp header when signing.

Approve only the tools a job needs, restrict the arguments that pick a target,
and prefer read-only credentials.

## Adding a connection type

A new kind of connection must:

- keep its credential outside the sandbox and out of the model's context,
- make every call through the broker, checked against the frozen plan,
- let the owner choose which tools jobs may use,
- refuse endpoints, credentials or scopes from repository configuration,
- send outbound requests through the shared address check.
