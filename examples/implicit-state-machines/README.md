# Implicit state machines

Find the state machines your application has without naming them. These are
status fields written from many places, booleans that describe one thing, and
timestamps that decide behavior. The report ranks which ones to make explicit
first. This example needs only a model provider. It reads the repository in
the offline sandbox and connects to nothing else. The job sets `history: 1`, so
each run reads the previous report and marks findings as new, still open, or
fixed.

The idea comes from Steve Ruiz, who suggested asking an agent to
["Find all of the implicit state machines in my app"](https://x.com/steveruizok/status/2116552016218710389).

## Try it

1. Copy this directory's `.secondshift.yaml` and
   `.agents/skills/implicit-state-machines` into the root of your repository
   and commit them. If the repository already has a `.secondshift.yaml`, add
   the `implicit-state-machines` job to it. SecondShift's own root manifest
   already includes this job.
2. In SecondShift, connect a model provider and choose its default model.
3. On **Repositories**, add your repository, or resync it if it is already
   there. The job appears on **Jobs**.
4. Open the job and choose **Run now**. The run page shows each step and the
   report when it finishes.

The job runs on the first day of each month at 08:00 UTC once you enable it.
Change `schedule` and `timezone` to suit you, or remove `schedule` to run it
only with **Run now**.

## Get the report somewhere else

To receive the report in chat or another service, add a webhook connection and
a `notify` entry to the job:

```yaml
    notify:
      - binding: engineering-reports
        required: false
```

Then approve the job's notification access. See
[Deliver reports to a webhook](../../docs/repository-configuration.md#deliver-reports-to-a-webhook) for the details.

## Make it yours

The skill is plain Markdown. Edit `SKILL.md` to point it at the parts of your
app that matter most, or to change how the report reads, then commit and
resync. Every run records the commit it used.
