# Deploy with Docker Compose

This single-server example runs SecondShift behind Caddy with HTTPS,
PostgreSQL and private artifact storage (S3 or a local persistent volume). It uses
[`compose.production.yaml`](../compose.production.yaml) on a Linux Docker host.

GitHub repositories are imported through a GitHub App, or a personal access
token. This example targets one
dedicated server and has downtime during updates. It does not provide high
availability.

## Prepare

Install Docker Engine with the Compose plugin, Git, and gVisor's
[`runsc` runtime](https://gvisor.dev/docs/user_guide/install/). Point a DNS
hostname at this server and allow inbound TCP 80/443 (optionally UDP 443 for
HTTP/3). Only Caddy publishes ports. Run these commands on the Docker host,
using Bash, from a checkout of the version you intend to deploy:

```bash
docker run --rm --runtime=runsc hello-world
umask 077
cp deploy/production/compose.env.example deploy/production/compose.env
```

Edit `deploy/production/compose.env`: set your hostname, three independent
passwords from `openssl rand -hex 32`, an absolute runner directory, and the
bucket's region, endpoint and credentials. Use hex database passwords so they
are also valid in the connection URLs. Keep values containing `$` in single
quotes in this env file to prevent Compose interpolation.

Create the private bucket separately. Its credential needs `s3:ListBucket` on
the bucket and `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on its objects.
Keep public access disabled. For an S3-compatible provider, use its HTTPS
endpoint and set `ARTIFACT_ADDRESSING_STYLE=path` if required. Web and worker
receive the read/write credential. The gateway reads webhook
payloads for subscription agents. Set both `ARTIFACT_READ_ACCESS_KEY_ID` and
`ARTIFACT_READ_SECRET_ACCESS_KEY` to a separate credential with only
`s3:GetObject` on the same objects.

For local storage, set `ARTIFACT_STORAGE=filesystem`; the S3 settings can be
left empty. Compose mounts the persistent `artifacts` volume on web and worker
and mounts it read-only on the gateway. Neither the runner nor agent containers
receive this volume or S3 credentials. Use one storage backend consistently;
changing it does not move existing artifacts.

Define a shortcut for the remaining commands, then pull the image and generate the
application keys. Replace the example hostname with the same one in the env file:

```bash
dc() { docker compose --env-file deploy/production/compose.env -f compose.production.yaml "$@"; }
docker pull ghcr.io/piotrekno1/second-shift:0.1.0b12
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/deploy/production:$PWD/deploy/production" \
  -w "$PWD/deploy/production" ghcr.io/piotrekno1/second-shift:0.1.0b12 \
  python -m secondshift.config.container_env --hostname secondshift.example.com
```

The published image supports Linux amd64 and arm64. All application services use
the same image. `SECONDSHIFT_IMAGE` in `compose.env` pins the release. Use the
same version in the configuration-generation command above. gVisor must still
be installed on the Docker host.

The generator creates private `web.env`, `gateway.env`, `worker.env` and
`runner.env` files. Keep their keys and service settings. Compose overrides their
SQLite defaults with PostgreSQL URLs and configures shared artifact storage.
Create the
`RUNNER_ROOT` directory you chose (`mkdir -p /srv/secondshift/runner` for the
example). The same absolute directory is mounted inside the runner because the
host Docker daemon mounts its sandbox workspaces.

Optionally configure email and GitHub sign-in in `web.env` using the
[environment reference](environment-variables.md). Without email, password
reset messages cannot be sent. Generate these files only once, and retain them
for upgrades.

## Optional subscription agents

Model API jobs need no agent image. To enable Claude Code and Codex subscriptions,
build the separate agent image from the same checkout as the application. On a
single server, an immutable local image ID is sufficient:

```bash
docker build -f secondshift/runner/agent_image/Dockerfile \
  -t secondshift-agent:production .
docker image inspect --format '{{.Id}}' secondshift-agent:production
```

Set `AGENT_IMAGE` in `compose.env` to the printed `sha256:...` ID. Alternatively,
push the image to your private registry and set `AGENT_IMAGE` to its immutable
`registry/name@sha256:...` reference. Pull that exact image on the Docker host
before enabling it. Create the server-owned resolver file inside `RUNNER_ROOT`
(the runner mounts this file read-only in agent containers):

```bash
# Replace these with DNS servers reachable from this server's Docker networks.
printf 'nameserver 1.1.1.1\nnameserver 1.0.0.1\n' > /srv/secondshift/runner/agent-resolv.conf
chmod 644 /srv/secondshift/runner/agent-resolv.conf
```

Use your configured runner path. Do not copy a host resolver that uses a
loopback-only stub such as `127.0.0.53`. gVisor's network stack cannot use Docker's
embedded DNS redirection, so agents use this resolver and reach the gateway at
its reserved address. Set `AGENT_SUBNET` and `AGENT_GATEWAY_ADDRESS` together if
the default `172.30.0.0/24` and `172.30.0.2` overlap an existing host route.
The gateway address must be inside the subnet. Image architecture must match
the runner host. The main
application release workflow does not publish this separate image.

Compose supplies the image and broker settings to the application roles and
runner, including existing installations whose private env files are retained.
It creates a dedicated bridge named by `AGENT_NETWORK` (default
`secondshift_agents`). Only the gateway and transient agent containers join it;
PostgreSQL, web, runner and worker stay on the application network. Give each
installation on one Docker host a distinct agent network name.

Agent containers can reach the internet for their provider and the gateway's
run-scoped MCP endpoint. This is not an outbound destination allowlist. Their
built-in CLI tools are disabled; repository commands still execute in the
separate offline gVisor sandbox. Agents receive only their own subscription
login and a run token, never artifact credentials or an artifact directory.

The gateway runs workspace tools for subscription runs, so it needs the
runner URL and token. Files generated by this release already contain them.
For files generated by an earlier release, copy the two lines from `worker.env`
into `gateway.env` without changing them:

```bash
grep -E '^SECONDSHIFT_RUNNER_SERVICE_(URL|TOKEN)=' deploy/production/worker.env \
  >> deploy/production/gateway.env
```

Without them, every workspace tool call in a subscription run fails with "The
approved tool could not complete."

After changing `compose.env` or `gateway.env`, run `dc up -d`, then add a
subscription on **Models**. Verify its login check and a run that reads a
repository file, including a webhook-triggered run that calls
`trigger_payload`. Real provider logins under gVisor require operator
validation; the automated deployment probe uses a deterministic test agent.
See [Agent subscriptions](agent-subscriptions.md) for credential ownership and
execution limits. Keep the old agent image available until active runs finish.

## Configure repository access

Choose a fine-grained personal access token (PAT) or a GitHub App. PAT-only
installations do not need any `SECONDSHIFT_GITHUB_APP_*` settings, OAuth callback,
or App registration. GitHub sign-in is separate and optional.

### Personal access token

On **Repositories → Connect GitHub**, paste a fine-grained PAT. Classic tokens
are refused. Use the **Create a token on GitHub** link and select only the
repositories this workspace should access. The token needs:

- **Contents: Read-only** for import, checkout and automatic sync.
- **Pull requests**, **Actions** and **Commit statuses: Read-only** for GitHub tools.
- **Contents** and **Pull requests: Read and write** for draft pull requests.
  Do not grant **Workflows**.

Saving checks the token's identity. After importing, use the connection's
**Check** action to check repository reach and read permissions. PAT write
permissions are confirmed only when publishing a draft. A read-only token can
sync successfully while draft publishing fails.

Tokens are encrypted with the existing application encryption key and stored
in PostgreSQL. Web and worker already share that key; no PAT belongs in env
files, repository configuration or runner settings. Worker polling works
without App credentials and picks up newly added PAT connections without a
restart. Preserve the encryption key during upgrades and restores.

When a token expires or is revoked, choose **Replace token** on **Connections**
and provide a replacement from the same GitHub account. An organization may
require approval before a token can access its repositories. See the
[GitHub access guide](github-app.md#use-a-personal-access-token) for details.

### GitHub App

Register a [GitHub App](github-app.md#register-the-app) with callback URL
`https://secondshift.example.com/github/callback/`, using your hostname.
Enable user authorization during installation, grant read-only repository
contents access, and disable webhooks.

Add the same five values to **both `web.env` and `worker.env`**:

```dotenv
SECONDSHIFT_GITHUB_APP_ID=123456
SECONDSHIFT_GITHUB_APP_SLUG=your-secondshift-app
SECONDSHIFT_GITHUB_APP_CLIENT_ID=Iv1.example
SECONDSHIFT_GITHUB_APP_CLIENT_SECRET=replace-with-client-secret
SECONDSHIFT_GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
```

Use the downloaded PEM contents with literal `\n` between lines for the key.
Keep these credentials out of `compose.env`, gateway and runner. GitHub sign-in
is optional and uses separate credentials. The generated web and worker files
already contain the matching private runner URL/token. Retain those values.
The generated HTTP timeouts allow synchronous imports. Caddy's default proxy
configuration imposes no response-header timeout.

## Start

```bash
dc config --quiet
dc up -d --wait postgres
dc run --rm --no-deps web python manage.py migrate --noinput
dc run --rm --no-deps runner python manage.py migrate --noinput \
  --settings=secondshift.config.settings.runner
dc up -d
dc ps
dc logs --tail 30 worker runner caddy
dc exec web python manage.py create_workspace "My team"
```

Open the one-time owner link printed by the last command. Caddy obtains and
renews the HTTPS certificate automatically. Check the public service:

```bash
curl --fail https://secondshift.example.com/-/health/
```

The worker should log `DBOS worker started`. Runner startup checks gVisor and
pulls the sandbox image, so its first start can take longer. Resolve startup
errors in the logs before using the deployment.

One PostgreSQL instance holds two databases, each owned by a separate login
role without superuser privileges. Web, gateway and worker share `secondshift`,
including DBOS workflow tables. The runner uses only `secondshift_runner`.
Neither login can connect to the other's database. The PostgreSQL administrator
password goes only to the database container. Splitting the application roles
into more databases would break their shared state.

Only the runner receives the Docker socket and runs as root. That socket gives
it control of the Docker host. Use a dedicated, trusted server.
It receives no application keys or S3 credentials. Command sandboxes still run
unprivileged under gVisor without network access.

Each sandbox is limited to 2 GiB of memory with no swap, 2 CPUs and 256
processes. Change these with `SECONDSHIFT_RUNNER_MEMORY`,
`SECONDSHIFT_RUNNER_CPUS` and `SECONDSHIFT_RUNNER_PIDS_LIMIT` in `runner.env`.
Under gVisor, a command that exceeds the memory limit ends the whole sandbox,
and the run fails with "The sandbox ran out of memory."

## Import and verify a job

As the workspace owner, open **Repositories → Connect GitHub** and either
connect a fine-grained PAT or authorize the App and grant repository access. Select its branch and import
it. The selected commit must include a valid `.secondshift.yaml` and referenced
skills. Connect the required model provider and services, review capabilities,
and start a manual run before enabling schedules. See the
[GitHub App guide](github-app.md#connect-and-run) for the complete flow.

Confirm the run checks out the expected commit, finishes successfully, and its
report and artifacts are readable. Push a change and verify **Resync** imports
it. Enabled repositories with automatic sync are also polled by the worker
every five minutes.

The worker keeps a Git mirror of each repository and a size-limited cache of
run snapshots. Both are caches, rebuilt from GitHub when missing. The runner
keeps no repositories. It holds each run's files only while the run lasts.
The configured artifact store holds run inputs and outputs. PostgreSQL stores
metadata, job definitions and history.
A rerun of a commit that a force push removed from GitHub can fail once the
cache no longer has it.

## Update and back up

Back up both PostgreSQL databases, all private env files, the runner directory,
the S3 bucket, and Caddy's named volumes. Preserve the encryption key: saved
credentials cannot be recovered without it. Use PostgreSQL backup tools rather
than copying its live data volume.

The database dumps hold saved credentials encrypted with
`SECONDSHIFT_ENCRYPTION_KEY`, and sign-in passwords only as hashes.
`config.tgz` holds that key and the other server secrets unencrypted.
The runner archive can contain subscription logins and is also a secret backup.
Anyone with the database dumps and encryption key can read saved credentials,
so the commands below write both secret archives to their own folder. Store
them separately from the data backups, for
example in your password manager or secret store. Without it, saved credentials
can't be restored. Keep the data backups private and off the server too: they
hold repository contents, run history and Caddy's TLS keys.

Stop the application services so the databases, runner directory and bucket
match, then run from the checkout:

```bash
RUNNER_ROOT=$(sed -n 's/^RUNNER_ROOT=//p' deploy/production/compose.env)
stamp=$(date +%Y%m%d-%H%M%S)
backup=/srv/secondshift-backups/$stamp
secrets=/srv/secondshift-secrets/$stamp  # store apart from the data backups
mkdir -p "$backup" "$secrets" && chmod 700 "$backup" "$secrets"
dc stop caddy web gateway worker runner
dc exec -T postgres pg_dump -U postgres -Fc secondshift > "$backup/secondshift.dump"
dc exec -T postgres pg_dump -U postgres -Fc secondshift_runner > "$backup/secondshift_runner.dump"
tar -czf "$secrets/config.tgz" deploy/production
tar -czf "$secrets/runner.tgz" -C "$RUNNER_ROOT" --exclude=./sessions .
for volume in artifacts caddy_data caddy_config; do
  docker run --rm -v "secondshift_$volume:/volume:ro" -v "$backup:/backup" \
    alpine:3.22 tar -czf "/backup/$volume.tgz" -C /volume .
done
```

For S3 storage, copy the bucket while the services are stopped, with your
provider's tools (for
example `aws s3 sync s3://your-bucket "$backup/s3"`), or rely on its versioning
or replication. Then start the services again:

```bash
dc up -d
```

To restore, prepare a server as in [Prepare](#prepare) with a checkout of the
release that made the backup, copy the backup and its `config.tgz` to it, and
restore the bucket if it was lost. PostgreSQL's volume must be empty: its first start
creates the roles and empty databases from the restored passwords.

```bash
backup=/srv/secondshift-backups/20261027-082400   # the backup to restore
secrets=/srv/secondshift-secrets/20261027-082400  # its config.tgz
tar -xzf "$secrets/config.tgz"
RUNNER_ROOT=$(sed -n 's/^RUNNER_ROOT=//p' deploy/production/compose.env)
runner_archive="$secrets/runner.tgz"
# Backups made before subscription support kept this archive with the data.
if [ ! -f "$runner_archive" ]; then runner_archive="$backup/runner.tgz"; fi
mkdir -p "$RUNNER_ROOT" && tar -xzf "$runner_archive" -C "$RUNNER_ROOT"
dc up -d --wait postgres
dc exec -T postgres pg_restore -U postgres -d secondshift --no-owner \
  --role=secondshift --exit-on-error < "$backup/secondshift.dump"
dc exec -T postgres pg_restore -U postgres -d secondshift_runner --no-owner \
  --role=secondshift_runner --exit-on-error < "$backup/secondshift_runner.dump"
dc create web caddy
for volume in artifacts caddy_data caddy_config; do
  docker run --rm -v "secondshift_$volume:/volume" -v "$backup:/backup:ro" \
    alpine:3.22 tar -xzf "/backup/$volume.tgz" -C /volume
done
dc run --rm --no-deps web python manage.py migrate --noinput
dc run --rm --no-deps runner python manage.py migrate --noinput \
  --settings=secondshift.config.settings.runner
dc up -d
```

Sign in and open an earlier run's report to confirm the restore. Update to a
newer release only after it works.

Before upgrading an existing GitHub App installation to use new GitHub tools
or draft pull requests, update its permissions:

- GitHub tools need **Pull requests**, **Actions** and **Commit statuses: Read-only**.
- Draft publishing needs **Contents** and **Pull requests: Read and write**.
- Each installation owner must accept the App's changed permissions on GitHub.

Read-only sync needs no write permission. PAT users instead replace or update
their token with the permissions for the features they intend to use. Check
repository access and perform a draft publication after changing permissions;
a successful token save alone does not prove write access. Enable draft pull
requests in the repository settings and, for saved jobs, in the job definition.
GitHub credentials used for source access also serve GitHub tools and publishing;
no runner credential or extra deployment service is needed.

For an update, take a backup, fetch the intended release, update
`SECONDSHIFT_IMAGE` in `compose.env` to that release version (or digest), and run:

```bash
dc stop caddy web gateway worker runner
dc pull
dc run --rm --no-deps web python manage.py migrate --noinput
dc run --rm --no-deps runner python manage.py migrate --noinput \
  --settings=secondshift.config.settings.runner
dc up -d
```

Retain private env files and encryption keys; do not rerun the environment
generator. Rebuild or pull the intended agent image and update `AGENT_IMAGE`
when upgrading subscription execution. Subscription runs also need the runner
URL and token in `gateway.env`; see
[optional subscription agents](#optional-subscription-agents). The existing PostgreSQL workflow
database remains shared; the separate SQLite workflow file applies only to
SQLite installations.

This update has downtime. Changing an env file also requires `dc up -d` to
recreate its container. PostgreSQL initialization runs only on an empty volume:
editing passwords in the env file does not rotate existing database passwords.
Keep the PostgreSQL major version fixed until you perform a database upgrade.
`dc down` retains named volumes. `dc down -v` deletes them.
