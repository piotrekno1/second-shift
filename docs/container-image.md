# Set up SecondShift on a Linux server

This guide starts SecondShift on one server, using SQLite for its databases,
a local volume for run files, and Caddy for HTTPS. You do not need to read or
change the application code. Run the commands in order in the same Bash session.

**Current limitation:** the dashboard and supporting services can be started,
but production repository import is not implemented yet. You cannot complete
the setup of a working repository job through this deployment today. For a
usable personal installation, follow [the local setup](../README.md#try-it-out-now).

## 1. Check your server

You need:

- A Linux server with Docker Engine, Git, Bash and curl installed, and permission
  to run Docker commands.
- gVisor installed as Docker's `runsc` runtime. Follow the
  [gVisor installation instructions](https://gvisor.dev/docs/user_guide/install/)
  if it is not installed yet.
- A hostname such as `secondshift.example.com`, with its DNS pointing to this
  server. Ports 80 and 443 must be available and reachable from the Internet.
  This guide assumes no other web server is using those ports.

Check Docker and gVisor:

```bash
docker version
docker run --rm --runtime=runsc hello-world
```

The second command must finish successfully. If Docker says `runsc` is unknown,
finish installing gVisor before continuing.

## 2. Choose a hostname and build the image

Replace `secondshift.example.com` below with your hostname. Leave out `https://`
and any trailing slash. `SECONDSHIFT_SETUP_HOSTNAME` is only a helper for these
setup commands. The generator writes the actual service settings. Keep this
terminal open for the remaining steps.

```bash
set -euo pipefail
umask 077
mkdir -p "$HOME/secondshift-server"
cd "$HOME/secondshift-server"
export SECONDSHIFT_SETUP_HOSTNAME=secondshift.example.com

git clone https://github.com/piotrekno1/second-shift.git source
docker build -t secondshift:local ./source
```

There is no published SecondShift image yet, so this builds one on your server.
You should see the build finish without an error. All remaining commands run
from `$HOME/secondshift-server`, outside the `source` directory.

## 3. Generate your configuration

Generate the configuration files with this command. It creates fresh keys and
writes each service's settings into its own private file:

```bash
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD:$PWD" -w "$PWD" --entrypoint python \
  secondshift:local -m secondshift.config.container_env \
  --hostname "$SECONDSHIFT_SETUP_HOSTNAME"
```

You should see `Created web.env, gateway.env, worker.env and runner.env`.

| File | Used by | What it configures |
| --- | --- | --- |
| `web.env` | Dashboard | Public address and application database |
| `gateway.env` | Model gateway | Application database and credential decryption |
| `worker.env` | Background worker | Job execution, internal services and report links |
| `runner.env` | Command runner | Sandbox execution and its separate database |

The command also creates a `runner/` directory. Keep these files private and
back them up. Generate them **once for a new installation**. The command refuses
to overwrite an existing setup. Reuse the same keys when restarting or upgrading.

You do not need to edit the generated defaults. The public address automatically
sets browser security and host checks for the dashboard. The gateway has its own
internal hostname. Only the worker and runner receive the runner's auth token.

For PostgreSQL, S3 storage or email delivery, make the changes described in the
[configuration reference](environment-variables.md) **before the next step**.
The rest of this guide works with the generated defaults.

## 4. Initialize the databases

Create the private network and persistent application volume:

```bash
docker network create secondshift
docker volume create secondshift-data
```

Initialize the application database:

```bash
docker run --rm --network secondshift --env-file web.env \
  -v secondshift-data:/var/lib/secondshift \
  secondshift:local python manage.py migrate --noinput
```

Initialize the runner's separate database:

```bash
docker run --rm --user 0:0 --env-file runner.env \
  -e DJANGO_SETTINGS_MODULE=secondshift.config.settings.runner \
  -v "$PWD/runner:$PWD/runner" \
  secondshift:local python manage.py migrate --noinput
```

Both commands must finish without an error. Messages ending in `OK` or
`No migrations to apply` are expected. These temporary containers exit after
initialization. Your data remains in the volume and `runner/` directory.

## 5. Start SecondShift

SecondShift uses four containers: the dashboard server, a model gateway, a
background worker, and an isolated command runner. Start the internal services
first:

```bash
docker run -d --name secondshift-runner --restart unless-stopped \
  --network secondshift --user 0:0 --env-file runner.env \
  -v "$PWD/runner:$PWD/runner" \
  -v /var/run/docker.sock:/var/run/docker.sock \
  secondshift:local

docker run -d --name secondshift-gateway --restart unless-stopped \
  --stop-timeout 610 --network secondshift --env-file gateway.env \
  -v secondshift-data:/var/lib/secondshift secondshift:local
```

Then start the dashboard and background worker:

```bash
docker run -d --name secondshift-web --restart unless-stopped \
  --stop-timeout 40 --network secondshift --env-file web.env -p 127.0.0.1:8000:8000 \
  -v secondshift-data:/var/lib/secondshift secondshift:local

docker run -d --name secondshift-worker --restart unless-stopped \
  --stop-timeout 60 --network secondshift --env-file worker.env \
  -v secondshift-data:/var/lib/secondshift \
  secondshift:local python manage.py run_dbos_worker
```

Each command prints a container ID. Only the runner receives Docker access.
never give it another role's env file or the `secondshift-data` volume. The matching
`runner/` paths on both sides of its mount are required for command execution.

Check that the internal services respond:

```bash
docker exec secondshift-web python -c '
from urllib.request import urlopen
for host, port in [("secondshift-gateway", 8001), ("secondshift-runner", 8002)]:
    with urlopen(f"http://{host}:{port}/-/health/", timeout=10) as response:
        print(host, response.status)
'
docker logs --tail 30 secondshift-worker
```

Both health checks should print `200`. The worker log should include
`DBOS worker started`. The runner's first start may take longer while it pulls
the command image. If a check fails, inspect `docker logs secondshift-runner`
or `docker logs secondshift-gateway` and resolve the error before continuing.

## 6. Enable HTTPS

Create a Caddy configuration for your hostname:

```bash
cat > Caddyfile <<EOF
$SECONDSHIFT_SETUP_HOSTNAME {
	reverse_proxy 127.0.0.1:8000
}
EOF

docker run -d --name secondshift-https --restart unless-stopped \
  --network host -v "$PWD/Caddyfile:/etc/caddy/Caddyfile:ro" \
  -v secondshift-caddy-data:/data -v secondshift-caddy-config:/config \
  caddy:2
```

Caddy [obtains and renews the HTTPS certificate](https://caddyserver.com/docs/automatic-https).
Wait for certificate setup, then check your public address:

```bash
curl --fail "https://$SECONDSHIFT_SETUP_HOSTNAME/-/health/"
```

This must return successfully without a certificate warning. If it does not,
check `docker logs secondshift-https`, your DNS record, and incoming access to
ports 80 and 443. Do not open ports 8000, 8001 or 8002 in your firewall.

## 7. Create your account

```bash
docker exec secondshift-web python manage.py create_workspace "My team"
```

Open the one-time owner link printed by the command. Create your account and
finish joining the workspace. You should arrive at the dashboard at
`https://your-hostname`. Do not share this owner link.

The server is now running. You can configure model/service connections, but
repository jobs remain blocked by the production-import limitation noted at
the start of this guide.

## After setup

Containers restart automatically after a server reboot. For a manual restart,
use `docker restart secondshift-web` (or the name of another container). Do not
repeat the configuration-generation or `docker run` steps.

Back up all four `.env` files, the `runner/` directory and the
`secondshift-data` volume. Stop the application containers before copying
SQLite databases. Also retain the Caddy volumes, which hold its certificates.

See [environment variables](environment-variables.md) for database, artifact
storage and email settings, or [HTTP configuration](environment-variables.md#http-server)
for server tuning. Changing an env file requires
recreating the affected container. A plain restart does not reload that file.
