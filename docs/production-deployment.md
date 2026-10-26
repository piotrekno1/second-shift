# Deploy with Docker Compose

This single-server example runs SecondShift behind Caddy with HTTPS,
PostgreSQL and an existing private S3 bucket. It uses
[`compose.production.yaml`](../compose.production.yaml) on a Linux Docker host.

GitHub repositories are imported through a GitHub App. This example targets one
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
endpoint and set `ARTIFACT_ADDRESSING_STYLE=path` if required. Only web and
worker receive these credentials.

Define a shortcut for the remaining commands, then build and generate the
application keys. Replace the example hostname with the same one in the env file:

```bash
dc() { docker compose --env-file deploy/production/compose.env -f compose.production.yaml "$@"; }
docker build -t secondshift:production .
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/deploy/production:$PWD/deploy/production" \
  -w "$PWD/deploy/production" secondshift:production \
  python -m secondshift.config.container_env --hostname secondshift.example.com
```

The generator creates private `web.env`, `gateway.env`, `worker.env` and
`runner.env` files. Keep their keys and service settings. Compose overrides their
SQLite defaults with PostgreSQL URLs and sets S3 on web and worker. Create the
`RUNNER_ROOT` directory you chose (`mkdir -p /srv/secondshift/runner` for the
example). The same absolute directory is mounted inside the runner because the
host Docker daemon mounts its sandbox workspaces.

Optionally configure email and GitHub sign-in in `web.env` using the
[environment reference](environment-variables.md). Without email, password
reset messages cannot be sent. Generate these files only once, and retain them
for upgrades.

## Configure repository access

Register a [GitHub App](github-app.md#register-the-app) with callback URL
`https://secondshift.example.com/-/github/callback/`, using your hostname.
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
already contain the matching private runner URL/token; retain those values.
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
it control of the Docker host; this example is for a dedicated, trusted server.
It receives no application keys or S3 credentials. Command sandboxes still run
unprivileged under gVisor without network access.

## Import and verify a job

As the workspace owner, open **Repositories → Connect GitHub**, authorize the
App, and grant access to the desired repository. Select its branch and import
it. The selected commit must include a valid `.secondshift.yaml` and referenced
skills. Connect the required model provider and services, review capabilities,
and start a manual run before enabling schedules. See the
[GitHub App guide](github-app.md#connect-and-run) for the complete flow.

Confirm the run checks out the expected commit, finishes successfully, and its
report and artifacts are readable. Push a change and verify **Resync** imports
it. Enabled repositories with automatic sync are also polled by the worker
every five minutes.

Repositories and pinned Git objects live under `RUNNER_ROOT/repositories` on
the server, not in S3. Temporary execution workspaces also use the runner disk.
S3 stores run artifacts; PostgreSQL stores metadata, job definitions and history.
Monitor disk space and preserve the runner directory: a force-pushed commit
may no longer be recoverable from GitHub.

## Update and back up

Back up both PostgreSQL databases, all private env files, the runner directory,
the S3 bucket, and Caddy's named volumes. Preserve the encryption key: saved
credentials cannot be recovered without it. Use PostgreSQL backup tools rather
than copying its live data volume.

For an update, take backups, fetch the intended release, and run:

```bash
dc stop caddy web gateway worker runner
docker build -t secondshift:production .
dc run --rm --no-deps web python manage.py migrate --noinput
dc run --rm --no-deps runner python manage.py migrate --noinput \
  --settings=secondshift.config.settings.runner
dc up -d
```

This update has downtime. Changing an env file also requires `dc up -d` to
recreate its container. PostgreSQL initialization runs only on an empty volume:
editing passwords in the env file does not rotate existing database passwords.
Keep the PostgreSQL major version fixed until you perform a database upgrade.
`dc down` retains named volumes; `dc down -v` deletes them.
