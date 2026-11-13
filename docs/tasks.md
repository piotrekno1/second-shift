# Run one-off tasks

[README](../README.md) · [Repository jobs](engineering-tasks.md) · [MCP reference](mcp.md)

Give a repository a task now and come back to its report later. Write instructions
directly or reuse a committed skill. You do not need a job in `.secondshift.yaml`.

For example, queue a documentation review before signing off, then open **Runs**
the next morning to read the findings. When a report is ready, check it before
acting on its recommendations or relying on changes to connected services.

## Before you start

- Add and enable the repository in **Repositories**.
- Choose a model provider or subscription connection in **Models**.
- Sign in as a workspace owner or admin to submit or cancel tasks. Members can
  read runs and reports.
- Keep the SecondShift installation and its worker running while you want tasks
  to execute. For local use, keep `secondshift up` running.

See the [local quickstart](https://second-shift.app/docs/quickstart/) for installation and the
[subscription guide](agent-subscriptions.md) for subscription connections.

## Write a task or use a skill

1. Open a repository and choose **New task**.
2. Leave **Write a task** selected and describe the outcome, scope, and format.
   For example:

   ```text
   Review README.md and the installation guide for a new contributor.
   Check commands against the repository's current configuration.
   Return a Markdown report with file references, concrete fixes, and anything
   you could not verify. Do not change files or connected services.
   ```

3. Alternatively, choose **Use a skill** and enter a committed `SKILL.md` path,
   such as `.agents/skills/docs-drift/SKILL.md`. Add context in the focus field
   if this run needs a narrower scope. A skill supplies reusable instructions;
   the additional text applies only to this run. To use a skill from another
   public repository, choose **Skill from GitHub**. See
   [Try a skill from GitHub](#try-a-skill-from-github).
4. Open **Run settings** if you want a task name, another branch or commit, or a
   delayed start. The default branch is the one followed by the repository.
5. Choose **Queue task**. SecondShift opens the run so you can follow it.

The exact repository commit is pinned when you submit, including for delayed
tasks. Later branch changes do not change the accepted task. A skill must exist
as a regular committed file at that revision; writing a task needs no skill file.

## Tools and permissions

Repository tools are included. Under **Tools and delivery**, select any connected
MCP or API tools the task needs. Only connected services that allow tasks without
per-job approval are listed. Their allowed tools and repository restrictions
still apply; a saved job's approval does not grant access to a one-off task.

If a service is missing, ask a workspace owner to check its connection and access
policy. Do not paste credentials into task instructions. See the
[security model](security-model.md) for how access is controlled.

You can also select a notification connection to send the finished report.
Reports remain available in **Runs** whether or not delivery is selected.
Disconnecting a service or changing consent prevents subsequent tool calls and
report deliveries that no longer have access.

## Try a skill from GitHub

You can try a skill someone else publishes before you trust it with a schedule.

1. Open a repository, choose **New task**, then **Skill from GitHub**.
2. Paste a link to the skill on GitHub, such as
   `https://github.com/acme/agent-skills/blob/main/skills/triage/SKILL.md`.
   This fills in the repository, path and branch.
3. Choose **Check skill**. SecondShift pins the skill to a commit and shows
   what it asks for. Nothing runs yet.
4. Read the list, and the `SKILL.md` itself, then choose **Queue task**. The
   commit you checked is the one that runs.

The list comes from the skill's text. It reads MCP tools named in the
frontmatter's `allowed-tools`, and names like `mcp__linear__create_issue`
anywhere in the file. Each one shows whether a task here can use it:

| Status | Meaning |
| --- | --- |
| Ready | A connection with that binding has the tools and skips approval. Its tools start selected. |
| Needs approval | The connection needs an owner's approval, so a task cannot use it. |
| Unavailable | The connection is not connected, or lacks a tool the skill names. |
| Not connected | No connection uses that binding. |

The list is a hint, not a promise. A skill can need things it never names.

The run works on your repository. The skill's folder is mounted read-only at
`/skill`, and it is never part of the run's changes. Commands still run
without network. The task gets only the tools you select.

### Access it asked for

Every run can tell you what it needed and did not have. The agent records
each missing service with the `report_missing_access` tool, and the run page
lists them under **Access it asked for**. Nothing is granted by this. Connect
the service, or change its approval setting, then run the task again.

### Link to a skill

A link to `/try` opens the task form with a skill filled in, for example
`https://secondshift.example.com/try?repository=acme/agent-skills&path=skills/triage/SKILL.md&ref=main`.
You choose the repository, check the skill and queue it yourself. Opening the
link runs nothing.

### Schedule it

After a run, choose **Schedule this skill** on the run page. It shows a job
for `.secondshift.yaml` that pins the commit the run used. Commit it to the
repository and approve its access as for any job. See
[skills from another repository](repository-configuration.md#skills-from-another-repository).

## Queue and delayed starts

Leave **Not before** empty to become eligible as soon as capacity is available.
To run later, enter a date and time in your browser's local timezone. The run page
shows the earliest start with its timezone. A past time is immediately eligible.

**Not before is an earliest start, not an appointment.** A task can wait longer
for another task or worker capacity:

- One ad hoc task per repository is dispatched at a time. Saved repository jobs
  keep their separate overlap rules.
- Eligible tasks are considered in requested-start order, then acceptance order.
  A future task does not block one that is already eligible.
- Server concurrency limits and the run's execution limits still apply. Timing a
  task does not reserve subscription quota or guarantee available provider quota.
- By default, a repository can have 100 unfinished ad hoc tasks, counting both
  queued and running tasks. A full queue refuses new submissions until a task
  finishes or is canceled.

The worker checks waiting tasks every 15 seconds by default. Accepted tasks
survive a restart or an execution-queue outage. Keep the installation running to
execute them; a queued task alone does not mean an agent has started.
Repository availability and the submitter's current workspace permission are
checked again before execution.

Operators can configure the queue and concurrency limits in the
[environment reference](environment-variables.md).

## Follow, cancel, and review

Open **Runs** and select the task. The run page shows its instructions, status,
earliest start, timeline, and report. Queued and running pages update live.

While an ad hoc task is **Queued**, choose **Cancel task** to stop it before
execution. This also works for a delayed task. Once execution starts, this
queued-task cancellation action is no longer available.

After it finishes, read the report and check the timeline for errors, unavailable
evidence, or tool activity. Open or download the stored output when available.
If the task failed, inspect the failure reason before submitting another task.
### Files and changes

Ask for files in the task, such as "save the findings as a CSV". The agent saves
them in `/outputs`, and the run page lists them under **Files**. Saved files
always download, so a browser never opens them as a page. SecondShift keeps up
to 100 files and 100 MiB per run. Links and other special files are skipped.
When the report links to a saved file, such as `[findings](/outputs/findings.csv)`,
the link downloads that file from the run.

If the agent edits repository files, **Files** shows how many changed. Open or
download the changes as a patch. Nothing is pushed to the repository unless the
task asks for a draft pull request. SecondShift keeps up to 20 MiB of changes,
and skips new files that match `.gitignore`.

### Draft pull requests

A task can open a draft pull request with the agent's changes. A workspace owner
first allows it in the repository's settings. The GitHub App also needs write
access, as [GitHub App access](github-app.md#draft-pull-requests) describes.
Then set `pull_request` to `true` when you submit the task.

- The draft targets the branch the task ran on. A task pinned to a commit
  targets the followed branch.
- The branch is named `secondshift/run-<id>`, or after the task when the
  GitHub connection hides [attribution](github-app.md#attribution). The report
  becomes the pull request description.
- The agent never gets the write token. SecondShift builds the commit from the
  stored patch after the sandbox stops, then pushes it.
- A run that fails or changes nothing opens no pull request. Its delivery shows
  why.
- CI is skipped on the first push, because nobody has reviewed the patch yet.
  After you review it, push a commit to the branch to run CI. An owner can let
  CI run at once for trusted repositories.
- Changes to `.github/workflows` are refused by GitHub, since SecondShift never
  asks for workflow access.

If publishing fails, choose **Retry** next to the delivery. A retry uses the
stored patch, so the agent does not run again.

If notification delivery failed, the report is still available in Runs. Choose
**Retry** next to the delivery under **Details** to send it again without
running the task again.

To repeat a one-off task, create a new task from the repository page. If you want
to reuse its instructions regularly, commit a skill and configure a
[repository job](engineering-tasks.md) for manual, cron, or webhook execution.

## Dispatch from a coding agent through MCP

Connect the agent to [SecondShift's MCP server](mcp.md) using a token with the
`run` scope. The token's creator must still have workspace run permission.
Find the repository ID in its dashboard URL: `/repositories/12` means ID `12`.
Use `list_bindings` to discover available connections and tools.

Call `submit_task` with written instructions:

```json
{
  "repository_id": 12,
  "definition": {"name": "Review setup instructions"},
  "input": "Review the installation guide against repository configuration. Return a Markdown report with file references and concrete fixes.",
  "idempotency_key": "setup-review-001"
}
```

Replace `12` with your repository ID. To reuse a skill, add
`"skill": ".agents/skills/docs-drift/SKILL.md"` inside `definition`. Add
`revision` at the top level to choose a branch or full commit SHA.

To try a skill from another public repository, call `inspect_skill` with its
`repository` and `path`. Then submit with `skill` set to that path and
`skill_source` set to the repository and the returned `commit_sha`:

```json
{
  "repository_id": 12,
  "definition": {
    "name": "Triage new bugs",
    "skill": "skills/triage/SKILL.md",
    "skill_source": {"repository": "acme/agent-skills", "ref": "4f2c1d0e8b7a6c5d4e3f2a1b0c9d8e7f6a5b4c3d"}
  },
  "idempotency_key": "triage-trial-001"
}
```

For a delayed start, add a timezone-aware `not_before` timestamp at the top
level, such as `"2026-11-04T01:00:00+01:00"`. Replace it with your intended time;
past timestamps are immediately eligible. The dashboard's local-time field and
MCP's timezone-aware timestamp describe the same earliest-start setting.

The response includes `task_id`, `run.id`, and `started`. Here, `started: true`
means a new run was accepted; it may still be queued. Reuse the same key and
identical arguments after a lost response to get the original run with
`started: false`. Use a new key for a new task, including intentional repeats.

Using the returned run ID (for example, `42`), call:

| Tool | Arguments | Purpose |
| --- | --- | --- |
| `get_run` | `{"run_id": 42}` | Read status, timeline, and usage. |
| `get_run_report` | `{"run_id": 42}` | Read the report when available. |
| `cancel_task` | `{"run_id": 42}` | Cancel before execution starts. |

Canceling an already canceled task returns its canceled state. Canceling a
running or finished task is refused. See the [MCP reference](mcp.md) for tool
selection, delivery fields, and the equivalent HTTP endpoints.
