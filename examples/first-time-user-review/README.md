# First-time user review

Use the [skill](.agents/skills/first-time-user-review/SKILL.md) to walk a web
app as five kinds of new user, from its first page to its first success. It
returns one list of problems, ranked by how many of the five users hit each one.
It reports problems and does not fix them.

The five users are an impatient one, a skeptic, a phone user on a 375 pixel
screen, a non-native English speaker and one who tries to break things. Edit
the list in the skill to match your own users.

The skill reviews a deployed test copy of the app, such as staging, through a
browser server you run. The walk and the report live in `SKILL.md`. The rules
for a deployed copy live in
[references/remote.md](.agents/skills/first-time-user-review/references/remote.md).

## Describe the app

Add a short section to your repository's `AGENTS.md` or `README.md`. The skill
reads it before each run:

```markdown
## App review
- First success: create a project and run its first job.
- Test addresses: https://staging.example.com
- Signed in when the page shows the account menu.
- Skip: billing pages.
```

The skill opens only hosts listed as test addresses, even when a request or a
webhook names another one. Endpoints never go in the manifest.

## Set up the browser

1. Save a signed-in browser state for a test account. Sign in once by hand
   with `npx playwright codegen --save-storage=auth.json <app address>`. The
   skill never types passwords, so the password stays on this machine.
2. Run a browser MCP server where SecondShift can reach it, and where the
   browser can reach the app. The sample uses the tool names of
   [Playwright MCP](https://github.com/microsoft/playwright-mcp). For other
   servers, replace the names in `.secondshift.yaml` and in the skill.

   ```bash
   npx @playwright/mcp --port 8931 \
     --isolated --storage-state auth.json \
     --shared-browser-context \
     --allowed-origins "https://staging.example.com" \
     --image-responses omit --output-dir ./qa-screenshots
   ```

   - `--isolated --storage-state` starts the browser signed in, from a clean
     profile.
   - `--shared-browser-context` shares the sign-in across MCP sessions. The
     page itself belongs to one session. SecondShift keeps one session with a
     custom MCP server for the whole run, so every call of a run, sub-agents'
     too, works on the same page.
   - `--allowed-origins` keeps the browser on the app. Playwright says it is
     not a security boundary, so use a test environment.
   - `--image-responses omit` saves screenshots to `--output-dir` and returns
     only the file name. Inline images are too large for a tool result.
3. Restart the server before each run, so a run never sees the last run's
   pages. Sign-ins the app refreshes during a run are not saved back to
   `auth.json`. Repeat step 1 when a run reports that the sign-in expired.
4. Add a custom MCP connection with binding `browser`, and choose the tools
   above. Leave out `browser_close`: closing the browser can lose the sign-in.
   In local mode, `http://localhost:8931/mcp` works with any token. A hosted
   SecondShift needs an HTTPS address and a bearer token. Playwright MCP checks
   neither, so put it behind a proxy that checks the token. Clicking and typing
   can change data in the app, so the access review marks them. Use a test
   environment, never production.
5. Optional: connect a tool that resets the app's test data, with binding
   `test-data`, and put its name in `.secondshift.yaml`. The skill calls it
   before each user. Without it, the report says the app was not reset.

Every run shares the one browser. Run one review at a time per browser server.

## Limits

- Each tool call has a time limit of about 10 seconds. A slow page can time
  out. The skill retries once, then lists it under flaky results.
- Every user starts signed in, so sign-up is walked only up to the password
  field. The report says so.
- Screenshots stay in `--output-dir` on the browser server. The report names
  each file.

## Run limits and sub-agents

Five walks take more than the defaults of 15 minutes, 24 turns and 150 tool
calls. On this job's page, start with an hour, 200 turns and 400 tool calls,
and adjust from the first runs. See
[run limits](../../docs/self-hosting.md#run-limits).

The job sets `subagents: 5`, so each user walks in a fresh agent that does not
know the route an earlier user found. Sub-agents count toward the run's
limits. Codex runs cannot start sub-agents yet. Then the main agent walks
every user itself, and the report says so. See
[sub-agents](../../docs/repository-configuration.md#sub-agents).

With `history: 1`, each run saves its problems as `findings.json` and the next
run reports what is new, fixed and still open.

## Try it

1. Copy this example's `.agents/skills/first-time-user-review` into your
   repository's `.agents/skills`, and merge this example's `.secondshift.yaml`
   into your root manifest. Copy the actual example directory, not the
   discovery symlink from this repository's root `.agents` directory.
2. Add the app section above, set up the browser, and commit the files.
3. Connect a model provider or an agent subscription. Import or resync the
   repository, approve the requested access, raise the run limits, and choose
   **Run now**.
4. Read the report and its coverage. Run it before releases, or trigger it
   after each staging deploy with a [webhook](../../docs/repository-configuration.md#webhook-triggers).
   A timer cannot tell when the first-run experience changed.

To try the skill once without a manifest, use **Try from a link** with a link
to its `SKILL.md`. It shows the browser tools it needs from the skill's
`allowed-tools`.

For local coding-agent use, copy the same skill and request it with the app
address. If your browser server is not named `browser`, change the
`mcp__browser__` prefix in `allowed-tools`. Installing a skill does not grant
access.

## Use in this repository

The canonical skill lives in this example. Root
`.agents/skills/first-time-user-review` is a relative symlink for agent
discovery. Copying a sample manifest does not enable a job or grant access.

This sample is not registered in the root manifest.
