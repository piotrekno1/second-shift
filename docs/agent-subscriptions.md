# Agent subscriptions

SecondShift can run a workspace's jobs through Claude Code or Codex, using a
person's own subscription instead of a model API key. It needs an agent image.
Without one, nothing changes.

## Development

Run `process-compose up`. Startup builds the `secondshift-agent:dev` image and
turns on subscriptions for every service. The runner resolves that tag to the
image's immutable `sha256:` ID, so no registry is needed. The first build
downloads both CLIs. Later starts use Docker's build cache.

Add your login on **Connections → Agent subscriptions**. If no model provider
works yet, that login starts running the workspace's jobs. **Settings** shows
what runs them.

## Single machine

`secondshift up` turns subscriptions on by default. Local mode defaults the
agent image to `secondshift-agent:local`. On each start, `up` builds that tag
from the files shipped in the package. The first start downloads both CLIs and
takes a few minutes. Later starts use Docker's build cache. If a build fails,
`up` uses the last image it built.

Servers must pin the agent image by digest. Only a local runner accepts a tag.
It looks up the tag's `sha256:` ID for each run and starts that exact image.

To start without subscriptions:

```sh
secondshift up --no-agents
```

Agent containers reach the gateway through `host.docker.internal`.

- With Docker Desktop, that name reaches the host's loopback. The gateway stays
  on `127.0.0.1`.
- With native Linux Docker, the runner adds that name to each agent container.
  The gateway also listens on the Docker bridge address, usually `172.17.0.1`.
  Other machines can't route to it.

The gateway never listens on every network for agents.

You don't need to set anything. To use your own image, set
`SECONDSHIFT_AGENT_IMAGE`, and `up` won't build one. To use another gateway
address, set `SECONDSHIFT_AGENT_BROKER_URL`. Its host name is allowed
automatically.

## Logins

Owners add logins on **Connections**.

- **Claude Code:** run `claude setup-token` and paste the token.
- **Codex:** run `codex login` with file credential storage and paste the
  contents of `~/.codex/auth.json`. A login kept only in the OS keychain must be
  switched to file storage first. Only the token fields are kept. Settings,
  endpoints, API keys and MCP servers in the file are dropped.

SecondShift checks the token's format when you save it. It does not contact the
provider, so the list says **Login saved** rather than claiming the login works.
An expired login fails its first run with a message to reconnect.

This version imports a login you already have. It has no browser or device-code
sign-in of its own.

## What runs jobs

**Settings → Run jobs with** decides what runs every job in the workspace. It is
either the model provider, billed per use, or one subscription login. There is
no per-job choice.

- New workspaces start on the model provider.
- The first login a workspace connects becomes the choice only when no model
  provider works.
- After that, only an owner changes the choice. Adding a model provider, or one
  failing its check, never moves runs to another provider or billing path.
- If the chosen login is disconnected, runs are refused with the reason.
- The list offers the model provider and your own logins. Only a login's owner
  can choose it. Choosing it lets every job in the workspace use it.
- Changes apply to new runs. Queued and past runs keep the choice they started
  with. Repository sync never changes it.

An expired or exhausted subscription fails the run. It never falls back to the
model API. Reconnect or wait for the allowance to reset, then start a new run.
Scheduled runs use the same choice.

## Isolation

Each subscription run starts one agent container next to the run's offline
command sandbox. The container:

- runs as a non-root user with a read-only root filesystem, no capabilities,
  resource limits and the configured OCI runtime
- mounts only that login's credential directory. It gets no repository, Docker
  socket or host home.
- has the CLI's own shell, file, web search and other built-in tools turned off

The agent reaches the run's tools only through a run-scoped MCP broker on the
gateway. These are the same workspace and approved external tools a model API
run gets. Workspace commands still run in the offline command sandbox.

Every broker request checks that the run is still active and within its
deadline, that the login is still connected, and that its owner is still a
workspace owner. Each tool call must be in the run's frozen plan and counts
against its tool call budget. A call the agent sends twice runs twice.

An agent run is never restarted automatically after a worker or runner failure,
because it may already have changed external services. Start a new run to retry.

## Credentials

- The control plane stores logins encrypted.
- To start a run, it seals the login to the runner's public key. Only the runner
  can read it.
- The runner keeps each login's CLI files in its private `agent-auth` directory,
  as the CLIs do on a laptop. Protect that directory and its backups as secrets.
- Logins never appear in run plans, workflow arguments, results, Docker
  arguments or environment, container logs or API responses.
- Codex can refresh its login during a run. A runner lock lets only one run use
  a login at a time. A second run with the same login fails instead of racing
  the refresh. Keep each login's runs on one runner host.

Disconnecting blocks the broker at once. It then asks the runner to stop the
login's containers and delete its files. If the runner can't be reached, the
login stays disconnected and you can disconnect again later. Leaving the
workspace or losing the owner role also blocks the broker. An agent that loses
access keeps going without tools until the run's time limit.

## Limits

Each login can have a run limit, set by its owner on **Connections**. It caps
how many runs use the login in a rolling 24 hours. A run past the limit is
refused with the reason, and a scheduled run fails with it. Runs that were
refused before they started don't count. There is no limit by default.

- The broker enforces the tool call budget and the run deadline.
- Claude Code also gets the run's maximum turns. Codex has no equivalent
  setting, so the deadline and tool budget bound it.
- Token budgets are checked when the CLI reports usage, which can be after the
  budget is passed. Use the model API when you need a strict token cap.
- Run events record usage and the CLI version. Model API spend totals don't
  include subscription use.
- Raw CLI output and errors are discarded. A failed run keeps only a reason,
  such as an expired login or exhausted usage.

Check each provider's terms before using subscriptions on a shared hosted
service.

## Status

The full path was tested locally with Docker's default runtime and a fake
login. A run went through checkout, the sandbox, the runner and the agent
container, the CLI reached the broker, and the run failed with the expected
reconnect message. Real provider logins and gVisor have not been tested end to
end.

A single-runner server can be set up by hand with the settings above on the
control plane and runner. The production environment generator does not set
them yet. Jobs that read webhook payloads also need the gateway to get the
worker's artifact storage settings, which the standard production Compose file
doesn't pass to it yet.

Before hosted use, SecondShift still needs:

- support for more than one runner per login
- login health checks
- a way to show subscription usage
