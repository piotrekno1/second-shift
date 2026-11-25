# Issue fixer

Use the [skill](.agents/skills/issue-fixer/SKILL.md) to fix one approved GitHub
issue per run and open the change as a draft pull request.

It pairs with review jobs that file issues, such as a monthly code review with
`open_issues`. A review proposes issues. A maintainer reads them and adds the
`fix-approved` label to the ones worth fixing. The fixer takes the oldest
approved issue, fixes it with a test, and opens a draft that closes the issue
when it merges.

## Evidence and setup

Open issues, their comments and labels, and pull requests with their reviews,
through the built-in GitHub connection. Each job reads only its own repository.

- The GitHub App or token needs read access to Issues and Pull requests for the
  tools, and write access to Contents and Pull requests for drafts.
- An owner allows pull requests in the repository's settings. Until then, runs
  still write their report, and the run says why no draft opened.
- Approve the job's request for `list_issues`, `issue_read` and
  `search_pull_requests`.
- Add `setup` commands that install the project's dependencies, so the agent
  can run the tests. See [setup commands](../../docs/repository-configuration.md#setup-commands).

## Why a label

The label is the approval. Only people with triage access can add it, so
nobody else can queue work for the fixer. It also keeps a person between a
review's findings and a pull request: some findings are wrong or not worth a
change. Issue text is still untrusted, because anyone who can comment writes
part of it. The skill uses it only to learn what is wrong.

## Suggested schedule

Weekdays at 07:00 UTC, one issue per run. A tick is skipped while a run is
still going. The skill skips an issue while a pull request for it is open.
`history: 5` lets each run skip issues earlier runs left alone.

Close a draft and keep `fix-approved` to ask for another attempt. The fixer
reads the closed pull request's comments and reviews first, so say there what
to do differently. With no reason given, it hands the issue back and asks
instead of guessing. Remove the label to stop. After two closed attempts, the
fixer hands the issue back.

The fixer takes only fixes that are quick to review. It hands an issue back,
with no pull request, when the fix needs a migration, changes more than about
80 lines or 3 files, changes concurrency or crash handling, or needs a design
choice. SecondShift then comments on the issue with what the fixer found and a
suggested plan, removes `fix-approved` and adds `needs-design`. Pick those up
in a session with a person.

Each draft opens with its risk, whether the checks passed, and where to look
in the diff.

**Begin with a manual run on one approved issue, review the draft, then enable
the schedule.**

## Use in another project

1. Copy this example's `.agents/skills/issue-fixer` into your repository's
   `.agents/skills`, and merge this example's `.secondshift.yaml` into your
   root manifest. Commit the files.
2. Create the `fix-approved` label, or set your own in `approved_issues.label`.
   The skill doesn't name a label. SecondShift tells the agent which one, and
   hands back only issues that have it.
3. Import or resync the repository, approve the requested access, and choose
   **Run now**.

## Use in this repository

The canonical skill lives in this example. Root `.agents/skills/issue-fixer` is
a relative symlink for agent discovery. The root manifest runs it every weekday
with setup for the Python and dashboard dependencies. It picks up issues from
the monthly code review.

The root job runs in the `python_node` [sandbox](../../docs/runners.md#sandbox-images),
because a fix may touch the backend, the dashboard or both. The default sandbox
image has no Node. The generated `runner.env` offers `python_node`, with Python
3.13 and Node 24, the versions CI uses, plus git and make, which the tests and
`make check` call. The setup installs uv itself and takes under a minute.
Without the environment, the job can't be enabled or run.
