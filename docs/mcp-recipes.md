# Vendor MCP servers

[README](../README.md) · [Repository configuration](repository-configuration.md) · [Security model](security-model.md)

Some vendors run an official MCP server. Connect one as a **Custom server**
under **Connections** → **Add connection**. SecondShift has no built-in
support for these vendors. Any MCP server that offers sign-in or a bearer
token connects the same way.

## Connect any MCP server

1. Find the vendor's MCP server URL. It usually ends in `/mcp`. Use a
   read-only URL if the vendor offers one.
2. Choose **Sign in** if the server offers it. Tick only the scopes the job
   needs, and prefer read scopes. SecondShift refuses the sign-in if the
   server grants more than you ticked. Without sign-in, use the narrowest
   bearer token the vendor offers.
3. Sign in as an account that can see only what jobs should see. The
   credential sets the reach. Unselected tools do not limit it.
4. Allow only the tools jobs need. Tools the server marks read-only start
   selected. **May change data** means the server did not mark the tool
   read-only. Read the tool's description before allowing it.
5. Add a `requires` block with the binding and the tools. Run the job once by
   hand and read the run's timeline before scheduling it.

## Example: Linear

Checked on 2026-11-01. The tools and `requires` block below are a sample for
a job that reads issues and projects. Pick the tools your own job needs.

| Setting | Value |
| --- | --- |
| Binding | `linear` |
| Server URL | `https://mcp.linear.app/mcp/readonly` |
| Sign-in | **Sign in**, with the `read` scope ticked |

Linear's `/mcp/readonly` endpoint offers only read tools, and the `read`
scope keeps the token away from Linear's write APIs. Use the plain `/mcp`
endpoint only for a job that must create or change issues.

Linear marks none of its tools read-only. No tool starts selected, and every
tool shows **May change data**. On this endpoint with the `read` scope, none
of them can change data.

This example allows these tools on the connection:

| Tool | Returns |
| --- | --- |
| `list_teams` | Teams in the workspace |
| `list_issues` | Issues, filtered by team, state, assignee, label, project or date |
| `get_issue` | One issue with its attachments and branch name |
| `list_comments` | Comments on an issue, project or document |
| `list_projects` | Projects in the workspace |
| `get_project` | One project, with its milestones and members on request |

```yaml
requires:
  mcps:
    - binding: linear
      tools:
        - list_teams
        - list_issues
        - get_issue
        - list_comments
        - list_projects
        - get_project
```

`list_issues` skips archived issues unless the job passes `includeArchived`.

**What the credential reaches.** The token acts as the person who signed in,
in the Linear workspace they signed in to. It can read everything that
person can see there, including private teams they belong to. Other tools on
this endpoint read that person's notifications (`get_notifications`), list
members with their email addresses (`list_users`, `get_user`), and fetch
images from issue text (`extract_images`). Allow them only for a job that
needs them.

## Example: Grafana

Checked on 2026-11-01 with `mcp-grafana` 2.1.1, Grafana 12.2, Prometheus and
Loki. The tools and `requires` block below are a sample for a job that triages
alerts. Pick the tools your own job needs.

Grafana's MCP server, `mcp-grafana`, runs next to your Grafana. It holds the
Grafana credential. SecondShift holds a separate token that only lets it call
the server.

| Setting | Value |
| --- | --- |
| Binding | `grafana` |
| Server URL | Where you run `mcp-grafana`, ending in `/mcp` |
| Sign-in | **Bearer token**, set to the server's `--server-auth-token` |

### Run the server

1. In Grafana, add a service account with the **Viewer** role, and create a
   token for it. Viewer can read dashboards, alert rules and data sources, and
   cannot change them.
2. Run `mcp-grafana` with that token and a second, random token for callers:

   ```sh
   docker run -d -p 8000:8000 \
     -e GRAFANA_URL=https://grafana.example.com \
     -e GRAFANA_SERVICE_ACCOUNT_TOKEN=<service account token> \
     grafana/mcp-grafana:2.1.1 \
     -t streamable-http --address 0.0.0.0:8000 \
     --disable-write \
     --server-auth-token <random token> \
     --allowed-hosts mcp-grafana.example.com \
     --loki-guardrail-mode enforce --loki-guardrail-max-range 24h
   ```

   - `--disable-write` removes the tools that change Grafana.
   - `--server-auth-token` refuses callers without it. Without it, anyone who
     can reach the server uses the service account.
   - `--allowed-hosts` lists the host names callers use. The server refuses
     others, so include the one in the URL SecondShift calls.
   - The Loki flags refuse a log query over 24 hours. The run sees a failed
     tool call, and the server logs why. A query returns at most 100 lines, set
     with `--max-loki-log-limit`. Version 2.1.1 has no matching limit for
     Prometheus queries. Prometheus's own query limits still apply.
3. Serve it over HTTPS. Outside local mode, SecondShift calls only HTTPS URLs.

### Connect it

Add a **Custom server** connection with the URL, such as
`https://mcp-grafana.example.com/mcp`, and the random token as the bearer
token. With `--disable-write`, the server marks every tool it offers
read-only, so every tool starts selected. Clear the ones jobs do not need.

This example allows these tools. A run read a Prometheus range and Loki error
lines for a 30 minute window with them.

| Tool | Returns |
| --- | --- |
| `list_datasources` | Data sources and their IDs, which the query tools take |
| `alerting_rules_read` | Alert rules, with their queries and state |
| `query_prometheus` | A PromQL query, instant or over a range |
| `list_prometheus_metric_names` | Metric names, to find the right one |
| `query_loki_logs` | Log lines from a LogQL query |
| `list_loki_label_names` | Log labels, to build a query |
| `search_dashboards` | Dashboards by title |

```yaml
requires:
  mcps:
    - binding: grafana
      required: false
      tools:
        - list_datasources
        - alerting_rules_read
        - query_prometheus
        - list_prometheus_metric_names
        - query_loki_logs
        - list_loki_label_names
        - search_dashboards
```

`required: false` lets the job run, and say Grafana was not checked, when the
connection is missing.

**What the credential reaches.** The service account reads every dashboard,
alert rule and data source a Viewer can see in its organization. Through the
data sources, that is all their metrics and logs. Other tools reach more:

- `grafana_api_request` calls any Grafana API path. The server marks it
  read-only, but it is not limited to reads.
- `search_docs` and `get_doc` fetch pages from the internet.
- The OnCall, Incident and Sift tools read people's schedules and incidents.

Allow them only for a job that needs them.

### Start a job from a Grafana alert

A job with `triggers: [webhook]` can start when a Grafana alert fires. See
[webhook triggers](repository-configuration.md#webhook-triggers) for how
triggers work.

1. Add the trigger and these settings to the job, and sync the repository:

   ```yaml
   triggers: [webhook]
   webhook:
     skip_when:
       status: resolved
     repeat_key: groupKey
     repeat_window: 1h
   ```

   Grafana sends `status: resolved` when an alert stops firing, and the same
   `groupKey` each time it repeats a notification for the same group.
2. On the job page, turn on the webhook trigger. Copy the URL and the secret.
3. In Grafana, add a contact point with the **Webhook** integration and that
   URL. Set **Authorization Header - Scheme** to `Bearer` and **Authorization
   Header - Credentials** to the secret.
4. Add a notification policy that routes the alerts to it. Keep an existing
   Slack contact point on the same policy, so people still get the alert.
5. Use **Test** on the contact point. The job page lists the request.

To sign requests instead of sending the secret, leave the Authorization header
empty. Under **HMAC Signature**, set **Secret** to the secret, **Header** to
`X-SecondShift-Signature` and **Timestamp header** to `X-SecondShift-Timestamp`.

Grafana must reach the trigger URL. A Grafana in Docker reaches a SecondShift
on the same machine at `host.docker.internal`. Add that name to
`SECONDSHIFT_ALLOWED_HOSTS`, or SecondShift answers 400.

## Other vendors

On 2026-11-01, SecondShift found sign-in settings on these servers. Nobody
completed a sign-in or ran a job against them. Follow the steps above and
check the tool list after connecting.

| Vendor | Server URL | Sign-in | Limiting access |
| --- | --- | --- | --- |
| Atlassian | `https://mcp.atlassian.com/v2/mcp?tools=all` | Offered | Tick read scopes only, such as `read:jira:agent-interface` and `read:confluence:agent-interface`. Keep `?tools=all`. Without it, the server offers generic discover and execute tools instead of named ones. |
| Datadog | `https://mcp.datadoghq.com/v1/mcp`. Other sites use their own host, such as `mcp.datadoghq.eu`. | Offered, with one scope, `mcp_all`. Datadog's docs also allow a personal or service access token as a bearer token. | Pick tools with `?toolsets=`. Only `core` is on by default. Users without the `mcp_write` permission get no write tools. |
| Notion | `https://mcp.notion.com/mcp` | Offered, with one scope, `default` | No read-only option is documented. The token reaches everything the person who signed in can see. |

SecondShift cannot sign in to PagerDuty's server
(`https://mcp.pagerduty.com/mcp`), because it does not let new apps register.
PagerDuty's docs send API tokens as `Token token=...`, and SecondShift sends
tokens as `Bearer`, so the token option was not tried.
