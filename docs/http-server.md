# HTTP server configuration

Set `SECONDSHIFT_SERVICE` to `web`, `gateway` or `runner`, then start the server:

```bash
SECONDSHIFT_ENV=production SECONDSHIFT_SERVICE=web uv run gunicorn \
  --config python:secondshift.config.gunicorn
```

Use the same command for each HTTP service, changing `SECONDSHIFT_SERVICE`.
Configure the [application environment](environment-variables.md) before starting.
The background worker uses `uv run python manage.py run_dbos_worker` instead.

| Variable | Meaning and default |
| --- | --- |
| `SECONDSHIFT_HTTP_BIND` | Listen address. Defaults to `0.0.0.0:8000` web, `:8001` gateway, `:8002` runner. |
| `SECONDSHIFT_HTTP_PROCESSES` | HTTP process count. Default 2 web/gateway, 1 runner. Does not change job concurrency. |
| `SECONDSHIFT_HTTP_TIMEOUT_SECONDS` | HTTP server worker timeout. Default 30 seconds. |
| `SECONDSHIFT_HTTP_GRACEFUL_TIMEOUT_SECONDS` | HTTP shutdown grace. Default 600 seconds gateway, 30 web/runner. |
| `SECONDSHIFT_HTTP_LOG_LEVEL` | HTTP server log level. Default `info`. |
| `SECONDSHIFT_HTTP_ACCESS_LOG` | Request log destination. Default `-` writes to stdout. An empty value disables request logs. |

These settings do not apply to the background worker. Its parallelism is set
by `SECONDSHIFT_RUN_CONCURRENCY` in `worker.env`.

Allow the process supervisor more time to stop the server than the configured
HTTP shutdown grace.

`secondshift up` and `process-compose` use this configuration with loopback
addresses for their local services. `secondshift up` also disables access logs,
uses warning-level server logs and runs one gateway process.
