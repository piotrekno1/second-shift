# Environment variables

Set `SECONDSHIFT_ENV=production` when deploying a server.
For development or tests, use `dev`, `local` or `test`.

Set this variable before starting the process. A local `.env` file cannot
select the environment. Docker's `--env-file` does set it.

To generate the needed env files, run:

```bash
uv run python -m secondshift.config.container_env \
  --hostname secondshift.example.com --directory "$HOME/secondshift-server"
```

This creates four files in `$HOME/secondshift-server`, with production mode,
service names and keys already set. It refuses to overwrite existing files.

## Edit the env files

| File | Service |
| --- | --- |
| `web.env` | Dashboard and sign-in |
| `gateway.env` | Internal model requests |
| `worker.env` | Scheduling and job execution |
| `runner.env` | Sandbox execution |

The tables below tell you which files need each setting. Keep the generated
keys and service-specific settings in their original files. After editing a
file, recreate its container to apply the changes. A restart does not reload it.

## Application and public address

| Variable | Services | Meaning and default |
| --- | --- | --- |
| `SECONDSHIFT_SECRET_KEY` | Web, gateway, worker | Required Django signing key. Keep the generated value identical in these three files. |
| `SECONDSHIFT_ENCRYPTION_KEY` | Web, gateway, worker | Required Fernet key for saved credentials. Keep the same value in all three and retain it in backups. |
| `SECONDSHIFT_DATABASE_URL` | Web, gateway, worker | Required shared application database. The generator uses `sqlite:////var/lib/secondshift/control.sqlite3`. For PostgreSQL, use `postgres://secondshift:password@db:5432/secondshift`. |
| `SECONDSHIFT_PUBLIC_URL` | Web, worker | Public HTTPS origin, e.g. `https://secondshift.example.com`. Used for sign-in callbacks and links in reports. No path, query, fragment or embedded credentials. Not needed by gateway. |
| `SECONDSHIFT_ALLOWED_HOSTS` | Web, gateway | Optional comma-separated hostnames, without schemes or ports. Web defaults to the public URL's hostname. The generated gateway file sets `secondshift-gateway`. |
| `SECONDSHIFT_CSRF_TRUSTED_ORIGINS` | Web | Optional extra browser origins, including `https://`. Defaults to the public URL. Normally leave unset. |
| `SECONDSHIFT_LOG_LEVEL` | All | Application log level, e.g. `INFO`, `WARNING` or `DEBUG`. Default `INFO`. |
| `SECONDSHIFT_DEBUG` | Web, gateway, worker | Default `false`. Production refuses `true`. Log verbosity is controlled separately. |
| `SECONDSHIFT_DATA_DIR` | All | Container-local persistent data directory. Image default `/var/lib/secondshift`. The generated runner file overrides it to the host-mounted runner directory. |

Changing a database URL does not move existing data. For PostgreSQL, provision
a database and account, then put the same URL in web, gateway and worker before
initializing a fresh installation. Do not reuse that account for the runner.

## Background work and internal services

These settings belong in `worker.env`, except the shared runner token:

| Variable | Meaning and default |
| --- | --- |
| `SECONDSHIFT_RUN_CONCURRENCY` | Number of job runs this background-worker process can execute concurrently. Default `2`. Independent of HTTP server process count. |
| `SECONDSHIFT_WORKFLOW_DATABASE_URL` | Optional DBOS workflow database URL, also needed by web when set. Defaults to `SECONDSHIFT_DATABASE_URL`. Back it up consistently with the application database. |
| `SECONDSHIFT_RUNNER_SERVICE_URL` | Required private runner address. Generated value: `http://secondshift-runner:8002`. |
| `SECONDSHIFT_RUNNER_SERVICE_TOKEN` | Required auth token of at least 32 characters. Present only in worker and runner, with identical values. Generated automatically. |
| `SECONDSHIFT_MODEL_GATEWAY_URL` | Required private model endpoint. Generated value: `http://secondshift-gateway:8001`. |

Do not publish the gateway or runner ports. The dashboard does not need either
internal endpoint or the runner token in its environment.

## Runner and sandboxes

Put these in `runner.env` unless the table names other services:

| Variable | Meaning and default |
| --- | --- |
| `SECONDSHIFT_RUNNER_ALLOWED_HOSTS` | Comma-separated runner hostnames. Generated value `secondshift-runner`. Separate from the dashboard/gateway hosts. |
| `SECONDSHIFT_RUNNER_DATABASE_URL` | Optional runner-only database. Defaults to SQLite in the runner work directory. Can use a separate PostgreSQL database/account. |
| `SECONDSHIFT_RUNNER_WORK_ROOT` | Persistent runner workspace and session directory. The generated absolute path must be mounted at the identical path on the Docker host. |
| `SECONDSHIFT_RUNNER_REPOSITORY_ROOT` | Runner's Git source store. Defaults to `repositories/` inside its work directory. Production repository import remains unfinished. |
| `SECONDSHIFT_RUNNER_RUNTIME` | Sandbox OCI runtime, not the HTTP server. Use `runsc` in web, worker and runner for production. Gateway does not need it. |
| `SECONDSHIFT_RUNNER_IMAGE` | Optional command-sandbox image. If overridden, keep the value identical in web, worker and runner. Default is the pinned Python image supplied by SecondShift. |
| `SECONDSHIFT_RUNNER_SANDBOX_UID`, `SECONDSHIFT_RUNNER_SANDBOX_GID` | Identity inside command sandboxes. Image defaults `10001:10001`. Separate from the runner container's root user. |

Only the runner receives the Docker socket. It must not receive the application
volume or another service's env file. Repository manifests cannot override any
of these sandbox choices.

## Email

Set these in `web.env` to send address confirmations and password resets:

```dotenv
SECONDSHIFT_EMAIL_URL=smtp+tls://user:password@mail.example.com:587
SECONDSHIFT_FROM_EMAIL=SecondShift <noreply@example.com>
```

| Variable | Meaning |
| --- | --- |
| `SECONDSHIFT_EMAIL_URL` | SMTP connection, including username, password, host and port. Use `smtp+tls://` for STARTTLS, commonly on port 587, or `smtp+ssl://` for implicit TLS, commonly on port 465. Unset by default, so production sends no email. |
| `SECONDSHIFT_FROM_EMAIL` | Sender address, optionally with a display name. Use an address approved by your mail provider. Default `webmaster@localhost`. |

These are the only email-specific environment variables. URL-encode special
characters in the SMTP username and password. Also set
[`SECONDSHIFT_PUBLIC_URL`](#application-and-public-address) to the public HTTPS
address so email links point to your server.

Recreate the web container, then request a password reset for an existing
account to check delivery. Without email configured, password resets are unavailable.

## Stored run files

Run files default to the shared filesystem volume. To use an existing private
S3 bucket, set these identically in **web and worker**. Gateway/runner do not
need artifact credentials:

```dotenv
SECONDSHIFT_ARTIFACT_STORAGE=s3
SECONDSHIFT_ARTIFACT_BUCKET=your-private-bucket
SECONDSHIFT_ARTIFACT_REGION=us-east-1
```

For an S3-compatible service, also set `SECONDSHIFT_ARTIFACT_ENDPOINT_URL` and,
if needed, `SECONDSHIFT_ARTIFACT_ADDRESSING_STYLE=path`. Supply
`SECONDSHIFT_ARTIFACT_ACCESS_KEY_ID` and `SECONDSHIFT_ARTIFACT_SECRET_ACCESS_KEY`,
or use the AWS credential chain (for example IAM roles). Bucket creation is
managed separately. Adding these variables does not upload existing local files.


For HTTP listen addresses, process counts, timeouts and logs, see
[HTTP server configuration](http-server.md).
