# How SecondShift handles your data

[README](../README.md) · [Security model](security-model.md) · [Repository configuration](repository-configuration.md)

SecondShift runs on servers you or your team control. This page says what it
reads, what it stores, and what leaves those servers.

## Your repository code

- SecondShift reads only committed files on the branch it follows. Edits that
  are not committed are never used.
- For a GitHub repository, the server fetches the branch with a short-lived,
  read-only GitHub App token. It keeps a copy of each synced commit in the
  runner's repository store.
- Each run gets its own copy of the repository at the run's commit, inside a
  sandbox with no network and no credentials. The copy is removed when the run
  ends.
- Each job's `SKILL.md` text is saved with the job so people can read it in the
  dashboard.

## Your keys and credentials

- Model keys, MCP and API credentials, OAuth tokens and webhook URLs are
  encrypted in the database with the server's `SECONDSHIFT_ENCRYPTION_KEY`.
- The dashboard never shows a saved key again. It shows only which service it
  belongs to, such as a webhook's host.
- Keys never enter the sandbox or the model's context. The
  [security model](security-model.md#where-secrets-live) lists which service
  decrypts each one, and when.

## What goes to the model provider

During a run, the model provider you connected receives:

- SecondShift's instructions and the job's task,
- the job's skill and, if the job sets `history`, its last reports,
- files and command output the agent reads in the sandbox,
- results of the tools the job is approved to use.

That can include any part of the repository. Use a provider your team
approves for that code, or a local model. Each run's **Input** file shows
exactly what the agent was given.

## What leaves your servers

Data leaves only through what a workspace owner sets up:

- the model provider, as above,
- MCP servers and HTTP APIs, for the tools a job is approved to use,
- webhooks, which get a run summary and a link. See the
  [webhook payload](repository-configuration.md#webhook-payload).

SecondShift sends no usage analytics or telemetry.

## How long it is kept

- Finished runs, their reports and files are deleted after
  `SECONDSHIFT_RUN_RETENTION_DAYS`, 90 days by default.
- Run files are stored on the server's volume, or in a private S3 bucket if the
  server is set up that way.
- Disconnecting or deleting a connection discards its stored credential.
