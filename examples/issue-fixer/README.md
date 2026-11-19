# Issue fixer

Use the [skill](.agents/skills/issue-fixer/SKILL.md) to fix one approved GitHub
issue per run and open the change as a draft pull request.

It pairs with review jobs that file issues, such as a monthly code review with
`open_issues`. A review proposes issues. A maintainer reads them and adds the
`fix-approved` label to the ones worth fixing. The fixer takes the oldest
approved issue, fixes it with a test, and opens a draft that closes the issue
when it merges.

## Evidence and setup

Open issues, their comments and labels, and open pull requests, through the
built-in GitHub connection. Each job reads only its own repository.

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
still going. `history: 5` lets each run skip issues earlier runs already
handled. The skill also skips an issue that an open pull request closes.

**Begin with a manual run on one approved issue, review the draft, then enable
the schedule.**

## Use in another project

1. Copy this example's `.agents/skills/issue-fixer` into your repository's
   `.agents/skills`, and merge this example's `.secondshift.yaml` into your
   root manifest. Commit the files.
2. Create the `fix-approved` label, or name another label in a **Run now**
   request.
3. Import or resync the repository, approve the requested access, and choose
   **Run now**.

## Use in this repository

The canonical skill lives in this example. Root `.agents/skills/issue-fixer` is
a relative symlink for agent discovery. The root manifest runs it every weekday
with setup for the Python and dashboard dependencies. It picks up issues from
the monthly code review.
