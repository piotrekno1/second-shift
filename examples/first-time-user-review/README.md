# First-time user review

Use the [skill](.agents/skills/first-time-user-review/SKILL.md) to walk a web
app as five kinds of new user, from its first page to its first success. It
returns one list of problems, ranked by how many of the five users hit each one.
It reports problems and does not fix them.

The five users are an impatient one, a skeptic, a phone user on a 375 pixel
screen, a non-native English speaker and one who tries to break things. Edit
the list in the skill to match your own users.

## Evidence and setup

A browser the job can drive, and a test or staging copy of the app with an
account prepared for the review.

1. Run a browser MCP server where SecondShift can reach it. The sample uses the
   tool names of [Playwright MCP](https://github.com/microsoft/playwright-mcp).
   For other servers, replace the names in `.secondshift.yaml` with theirs.
2. Limit the server to the app's test address, with Playwright MCP's
   `--allowed-origins` option, so the job cannot browse anywhere else.
3. Sign the browser in to a test account before the run, through the browser
   server's saved session or profile. The skill never types passwords.
4. Add a custom MCP connection with binding `browser`, and choose the tools
   above. Clicking and typing can change data in the app, so the access review
   marks them. Use a test environment, never production.
5. Put the test address in the request, or document it in the repository, for
   example in the README. Endpoints never go in the manifest.

Five walks take more steps than most jobs. Raise this job's model call and tool
call limits on its job page, within the server maximums. If the limits stop a
run early, the report names the users it skipped.

## When to run

Run it before a release, or after a change to sign-up, onboarding or the first
steps in the app. The sample has no schedule, because a timer cannot tell when
the first-run experience changed. Add a weekly schedule if your team ships
often.

**Begin with a manual run, check its findings and coverage, then enable a
schedule if useful.**

## Use in another project

1. Copy this example's `.agents/skills/first-time-user-review` into your
   repository's `.agents/skills`, and merge this example's `.secondshift.yaml`
   into your root manifest. Copy the actual example directory, not the
   discovery symlink from this repository's root `.agents` directory. Commit
   the files.
2. Connect a model provider and choose a default model. Set up the browser
   connection described above. Connection endpoints and credentials belong in
   SecondShift, never in the manifest or skill.
3. Import or resync the repository, approve the requested access, and choose
   **Run now**. Review the findings and coverage before adding a schedule.

For local coding-agent use, copy the same skill and request it explicitly with
the app address. Use that agent's approved browser tools. A coding agent that
can start subagents can run the five users in parallel and merge their results.
Installing a skill does not grant access. The skill returns a report and leaves
delivery to its host. See [repository configuration](../../docs/repository-configuration.md)
for connections, schedules, and optional webhook delivery.

## Use in this repository

The canonical skill lives in this example. Root
`.agents/skills/first-time-user-review` is a relative symlink for agent
discovery. Copying a sample manifest does not enable a job or grant access.

This sample is not registered in the root manifest.
