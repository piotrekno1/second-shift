# Documentation drift

Find links, paths, commands, and settings in your docs that no longer match the
repository. This example needs only a model provider. It reads the repository
in the offline sandbox and connects to nothing else. The job sets `history: 1`,
so each run reads the previous report and marks drift as new, still open, or
fixed.

## Try it

1. Copy this directory's `.secondshift.yaml` and `.agents/skills/docs-drift`
   into the root of your repository and commit them. If the repository already
   has a `.secondshift.yaml`, add the `docs-drift` job to it. SecondShift's own
   root manifest already includes this job.
2. In SecondShift, connect a model provider and choose its default model.
3. On **Repositories**, add your repository, or resync it if it is already
   there. The job appears on **Jobs**.
4. Open the job and choose **Run now**. The run page shows each step and the
   report when it finishes.

The job runs every Monday at 08:00 UTC once you enable it. Change `schedule`
and `timezone` to suit you, or remove `schedule` to run it only with
**Run now**.

## Get the report somewhere else

To receive the report in chat or another service, add a webhook connection and
a `notify` entry to the job:

```yaml
    notify:
      - binding: docs-reports
        required: false
```

Then approve the job's notification access. See
[Deliver reports to a webhook](../../docs/repository-configuration.md#deliver-reports-to-a-webhook) for the details.

## Make it yours

The skill is plain Markdown. Edit `SKILL.md` to change what it checks or how the
report reads, then commit and resync. Every run records the commit it used.
