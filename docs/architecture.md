# Architecture

[README](../README.md) · [Security model](security-model.md) · [Self-hosting](self-hosting.md)

## Execution design

SecondShift keeps four choices separate:

| Concern | Example | Controlled by |
| --- | --- | --- |
| Skill | `repository-health` | Repository author |
| Agent | SecondShift Agent | Workspace policy |
| Model | Claude, GPT or a local model | Workspace owner |
| Sandbox | Docker or `runsc` | Server operator |

The manifest selects a skill and requests capabilities. It cannot choose an
agent executable, credential, image, mount or network. The server records all
effective choices in an immutable execution plan. The agent is
provider-neutral.

## Runtime architecture

```text
DBOS scheduler -> Agent orchestrator -> Model gateway -> Model provider
                         |
                         +-> Tool broker -> Approved MCP servers and APIs
                         |
                         +-> Runner service -> Networkless run sandbox

Run finished -> Worker -> Webhook
```

- The orchestrator runs the agent loop but never receives a Docker socket or
  provider key.
- The runner alone owns the container runtime. Each run gets an ephemeral,
  resource-limited sandbox with no network or credentials.
- The gateway enforces model access, budgets and usage metering.
- The broker executes only approved MCP tools and API operations.
- DBOS checkpoints each turn and deduplicates retried calls. Large output goes
  to private artifact storage.

## Capability and policy boundary

A run receives only tools that are requested by the repository, available on a
workspace connection, and explicitly granted. Missing or incomplete required
access stops the run before dispatch.

Repository content and model output are untrusted. Commands stay inside the
networkless sandbox. MCP and API calls go through the broker, which checks them
against the frozen execution plan.

Model keys are decrypted only by the gateway. MCP and API credentials are
decrypted by the worker for each broker call. No credential appears in
repository configuration, workflow arguments, execution plans or command
sandboxes. The sandbox and broker enforce the security boundary. Shell
allowlists do not.

See the [security model](security-model.md) for where each secret lives and
the risks that remain.

