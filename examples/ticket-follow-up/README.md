# Ticket follow-up brief

Use the [skill](.agents/skills/ticket-follow-up/SKILL.md) to turn repository
history into a list of tickets that may be ready to close or update. It explains
what was implemented, suggests the next ticket update, and flags anything that
still needs testing or confirmation. It does not change tickets or add comments.

Choose this for engineering leads, triage owners, or teams whose definition of
done needs human judgment. Use [ticket reconciliation](../ticket-reconciliation/README.md)
when the team wants approved automatic updates under a defined workflow.

## Setup

1. Copy this example's actual `.agents/skills/ticket-follow-up` directory into
   your repository's `.agents/skills`, then merge its `.secondshift.yaml` into
   your root manifest and commit the files.
2. Connect a model provider. The sample requests GitHub read tools for commits
   and merged PRs. Connect binding `github` and approve those operations, or
   replace that declaration with equivalent source-control operations. Local
   Git history also works in environments where it is available. SecondShift's
   exported sandbox does not contain `.git`.
3. Connect your issue tracker through a custom MCP or HTTP API connection.
   Select read operations for tickets, current status, acceptance criteria,
   blockers, and linked work. Add their exact tool names under `requires` with
   `required: true`, then approve the job's access. The template intentionally
   omits tracker operation names because providers differ. It is incomplete
   until you add them. Built-in GitHub tools do not supply issue-tracker access.
4. Specify the repository, branches, and tracker projects in the task guidance.
   Add the team's definition of done when available. Without it, the brief
   identifies evidence and questions rather than claiming tickets are complete.
5. Import or resync, approve access, and choose **Run now**. Check ticket
   associations and the usefulness of the proposed updates.

**Begin with a manual run, check its results and tool activity, then enable a
schedule if useful.** The sample proposes weekdays at 09:00 UTC and reviews
the preceding seven days. Overlapping windows can repeat candidates until the
tracker is updated. Remove `schedule` for manual use only.

## Deliver to Slack

Create a webhook connection for the intended channel with binding
`ticket-follow-ups`, then add this to the job and approve notification access:

```yaml
    notify:
      - binding: ticket-follow-ups
        required: true
```

Keep the webhook URL in the workspace connection. SecondShift delivers the
run summary and a link to the stored report. The skill does not post to Slack
itself. This notification is a brief, not an interactive approval mechanism.
See [notification delivery](../../docs/repository-configuration.md#deliver-reports-to-a-webhook).

## Expected result

The report lists each candidate's ticket link, current state, proposed update,
source links, satisfied criteria, and evidence still needed. It separates
likely-complete work from partial or blocked work and states that nothing was
changed. Missing history or tracker access is reported as incomplete coverage.

The canonical skill lives in this example with a discovery symlink at root
`.agents/skills/ticket-follow-up`. The job is not registered in the root manifest.
