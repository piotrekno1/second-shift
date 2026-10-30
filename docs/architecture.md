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
- Skills can read or change external service data through approved operations.
  The built-in GitHub and Sentry tools are read-only. Custom connections can
  expose write operations chosen by the workspace owner.
- DBOS checkpoints each turn and deduplicates retried calls. Large output goes
  to private artifact storage.

## Repository data flow

```text
Control plane (holds the GitHub token)            Runner (no credentials)
--------------------------------------            -----------------------
Sync
  fetch new commits into the repository's mirror
  read .secondshift.yaml and skills at the commit
  save the commit as the repository's revision

Run start
  build a snapshot of the run's commit       HEAD /v1/sources/{sha256}
  (cached, reused for the same commit)  ---> skip the upload if present
                                             PUT /v1/sources/{sha256}
                                        ---> check the bytes match the name
                                             POST /v1/sessions
                                        ---> unpack into a new workspace
                                             start the networkless sandbox

Run end                                      DELETE /v1/sessions/{id}
                                        ---> remove the sandbox and workspace
```

- The GitHub token never leaves the control plane. The runner never talks to
  GitHub.
- A snapshot is the files of one commit, built from Git objects. It has no Git
  history. The repository's `.gitattributes` cannot hide or change files in it.
- The runner keeps no repositories. It deletes snapshots an hour after their
  last use.
- The mirror and snapshots are caches on the control plane. Deleting them only
  costs a fetch from GitHub. A rerun of a commit that a force push removed from
  GitHub can fail once the cache no longer has it.
- Local repositories skip the mirror. Their snapshots are built from the folder
  on disk.

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

