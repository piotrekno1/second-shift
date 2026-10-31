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

1. Save a signed-in browser state for the test account. Sign in once by hand
   with `npx playwright codegen --save-storage=auth.json <app address>`. The
   skill never types passwords, so the password stays on this machine.
2. Run a browser MCP server where SecondShift can reach it, and where the
   browser can reach the app. The sample uses the tool names of
   [Playwright MCP](https://github.com/microsoft/playwright-mcp). For other
   servers, replace the names in `.secondshift.yaml` with theirs.

   ```bash
   npx @playwright/mcp --port 8931 \
     --isolated --storage-state auth.json \
     --shared-browser-context \
     --allowed-origins "https://staging.example.com" \
     --image-responses omit --output-dir ./qa-screenshots
   ```

   - `--isolated --storage-state` starts every run signed in, from the same
     clean state.
   - `--shared-browser-context` keeps the page open between tool calls.
     SecondShift opens a new MCP session for each call, so without it every
     click starts on a blank page.
   - `--allowed-origins` keeps the browser on the app. Playwright says it is
     not a security boundary, so use a test environment.
   - `--image-responses omit` saves screenshots to `--output-dir` and returns
     only the file name. Inline images are too large for a tool result.
3. Restart the server before each run, so a run never sees the last run's
   pages or cookies. Saved sign-ins expire. Repeat step 1 when runs start on a
   sign-in page.
4. Add a custom MCP connection with binding `browser`, and choose the tools
   above. In local mode, `http://localhost:8931/mcp` works. A hosted
   SecondShift needs an HTTPS address and a bearer token. Playwright MCP checks
   neither, so put it behind a proxy that does. Clicking and typing can change
   data in the app, so the access review marks them. Use a test environment,
   never production.
5. Put the test address in the request, or document it in the repository, for
   example in the README. Endpoints never go in the manifest.
6. Optional: connect a tool that resets the app's test data. Add its binding
   and tool to the job's `requires`, then approve it. The skill calls it before
   each user. Without one, the report says
   the app was not reset.

Screenshots stay in `--output-dir` on the browser server. The report names
each file.

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
