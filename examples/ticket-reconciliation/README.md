# Reconcile implemented tickets

Use the [skill](.agents/skills/ticket-reconciliation/SKILL.md) to update or close
tickets when linked repository changes and validation evidence meet your team's
workflow. It verifies current ticket state, uses allowed transitions, and reads
back changes before reporting success.

Choose this for teams with explicit completion rules and approved automation.
Use the [ticket follow-up brief](../ticket-follow-up/README.md) when a person
should decide the next action. These are separate jobs with separate grants.
The brief does not implicitly authorize the action job.

## Setup

1. Copy this example's actual `.agents/skills/ticket-reconciliation` directory
   into your repository's `.agents/skills`. Merge its sample manifest into your
   root `.secondshift.yaml` and commit the files.
2. Connect a model provider and source-control access. The sample requests
   GitHub read tools. Replace that declaration for another provider with its
   actual operations. Local Git history is usable when available, but is absent
   from SecondShift's exported sandbox.
3. Connect the tracker through a custom MCP or HTTP API connection. Select
   ticket and comment read operations, available-transition lookup, and the
   transition operation. Add the exact operation names under `requires` with
   `required: true` and approve this job's request. Add comment creation only if
   your workflow permits evidence comments. Built-in GitHub and Sentry tools
   are read-only and do not implement this workflow.
4. Provide the task policy in the skill's repository guidance, such as a linked
   `docs/ticket-workflow.md`. It must identify repository branches, tracker
   projects, ticket types and eligible states, allowed target transitions,
   required completion evidence, and whether evidence comments are allowed.
   Use your tracker's actual state meanings. Do not put secrets or endpoints
   in the file. Connection setup belongs to the workspace.
5. Import or resync and run manually against the intended bounded scope. Verify
   the changed ticket state and recorded evidence before enabling its schedule.

The manifest intentionally omits provider-specific tracker operation names.
It is a setup template until you declare them. Missing policy, insufficient
evidence, or unavailable required operations prevents writes.

**Begin with a manual run, check its results and tool activity, then enable a
schedule if useful.** The sample proposes weekdays at 09:00 UTC with a seven-day
lookback. Start with the read-only brief if you want to inspect candidates
before granting writes. It is a separate workflow, not a per-ticket approval UI.

## Define what permits closing a ticket

For example, a team might require a change merged into its release branch,
passing validation linked to that revision, a successful deployment to the
required environment, all acceptance criteria satisfied, and no open blockers.
Another team might define done as merged and verified, with deployment tracked
separately. Document the actual rule rather than assuming one from a commit's
"fixes" message. If deployment is required, provide approved evidence access.

Explicit ticket references establish which work to inspect. They do not prove
the entire ticket is complete. Reverts, partial implementations, and unrelated
acceptance criteria can prevent an update.

## Repeated runs and failure handling

The skill skips already-completed transitions, checks for existing evidence
comments, and verifies writes. It checks state after a timeout before retrying.
Partial completion is reported so a later run can resume from observed state.
Conditional writes and idempotency depend on the tracker. Without them,
read-before-write cannot eliminate all races or duplicates under concurrency.
The skill avoids comments when it cannot check for duplicates reliably.

The summary separates verified changes, skipped tickets, partial failures, and
unknown outcomes. Notification delivery can use a separately approved webhook.
See [task setup](../../docs/engineering-tasks.md#tasks-that-change-service-data)
and [remaining risks](../../docs/security-model.md#remaining-risks).

The canonical skill lives here with a root discovery symlink at
`.agents/skills/ticket-reconciliation`. This job is not registered in the root
manifest, and adding the sample does not enable it or grant tracker access.
