# Alert triage

Use the [skill](.agents/skills/alert-triage/SKILL.md) to triage an alert as
soon as it fires. A webhook from your alerting tool starts the run. The skill
reads the alert, checks metrics, logs and errors around its start time, and
reports a likely cause with its evidence. It changes nothing.

## Evidence and setup

The alert itself, from the request that started the run, plus whichever of
these the job can reach:

- **Grafana**, through `mcp-grafana`, for Prometheus metrics, Loki logs, alert
  rules and dashboards. Follow the [Grafana recipe](../../docs/mcp-recipes.md#example-grafana)
  to run the server, connect it with binding `grafana`, and point a Grafana
  contact point at the job.
- **Sentry**, through the built-in Sentry connection with binding `sentry`,
  for issues and errors.
- A notification connection with binding `alerts-slack`, such as a Slack
  incoming webhook for your alerts channel.

Both data sources are optional. A run without one still reports, and says the
source was not checked.

## How it starts

The job has no schedule. It runs when the trigger receives an alert, or with
**Run now**, which has no alert and says so.

The `webhook` block ignores Grafana's `resolved` notifications, and repeats of
the same alert group for an hour after its run. Grafana sends these by
default. Each trigger also starts at most 10 runs an hour unless an owner
changes it. See [webhook triggers](../../docs/repository-configuration.md#webhook-triggers).

Keep your existing Slack contact point next to the webhook in the same
notification policy. People still get the alert right away, and the triage
report follows a few minutes later.

The alert's labels and annotations are written by whoever edits alert rules.
The skill treats them as data, and the run shows the exact alert it received on
its **Input** tab.

## Use in another project

1. Copy this example's `.agents/skills/alert-triage` into your repository's
   `.agents/skills`, and merge this example's `.secondshift.yaml` into your root
   manifest. Copy the example directory, not the discovery symlink from this
   repository's root `.agents` directory. Commit the files.
2. Connect a model provider that supports tool calling, and choose a default
   model. Connect Grafana, Sentry and the alerts channel as described above.
   Endpoints and credentials belong in SecondShift, never in the manifest or
   skill.
3. Import or resync the repository and approve the requested access. Turn on
   the webhook trigger on the job page and add its URL and secret to your
   alerting tool.
4. Send a test alert. Read the run's report and timeline before relying on it.

For another alerting tool, change `skip_when` and `repeat_key` to its own
field names. The skill reads any alert body.

## Use in this repository

The canonical skill lives in this example. Root `.agents/skills/alert-triage`
is a relative symlink for agent discovery.

This sample is not registered in the root manifest.
