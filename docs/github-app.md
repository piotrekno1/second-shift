# GitHub repository access

SecondShift reads repositories and syncs their committed `.secondshift.yaml`
files and skills through a GitHub App or a fine-grained personal access token.
Both are separate from GitHub sign-in.

The App is the better choice for a team. Each step gets its own short-lived
token, limited to one repository and to the access that step needs. A token
suits a single machine, or an organization where you cannot install the App.
See [Use a personal access token](#use-a-personal-access-token).

## Register the App

Register an App under your GitHub account or organization in **Settings →
Developer settings → GitHub Apps → New GitHub App**.

- Homepage URL: your public SecondShift HTTPS address.
- Callback URL: `https://secondshift.example.com/github/callback/`.
- Enable **Request user authorization (OAuth) during installation**.
- Leave the setup URL empty. Installation uses the authorization callback.
- Disable webhooks for now. The worker polls for changes instead.
- Repository permissions: **Contents: Read-only**. GitHub includes metadata read
  access automatically. No organization or account permissions are needed.
  For draft pull requests, see [below](#draft-pull-requests). For GitHub tools
  in jobs, see [GitHub tools](#github-tools).
- Choose whether installation is limited to your account or available to others.

Generate a client secret and a private key. Record the App ID, URL slug, and
client ID. GitHub's [registration guide](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app)
explains these options.

## Configure SecondShift

Set these variables on **web and worker only**, using private environment files
or your deployment's secret manager:

```dotenv
SECONDSHIFT_GITHUB_APP_ID=123456
SECONDSHIFT_GITHUB_APP_SLUG=your-secondshift-app
SECONDSHIFT_GITHUB_APP_CLIENT_ID=Iv1.example
SECONDSHIFT_GITHUB_APP_CLIENT_SECRET=replace-with-client-secret
SECONDSHIFT_GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
```

The private key accepts literal `\n` escapes or real newlines. Keep it out of Git.
`SECONDSHIFT_PUBLIC_URL` must match the public origin used in the callback URL.
Existing GitHub login OAuth credentials are independent of these App credentials.

The web process imports repositories through the internal runner API. Newly
generated container role files include its runner URL/token. For existing role
files, copy `SECONDSHIFT_RUNNER_SERVICE_URL` and
`SECONDSHIFT_RUNNER_SERVICE_TOKEN` from `worker.env` into `web.env`. Keep the
runner API private. Neither web nor worker needs the Docker socket or runner DB.

Imports are synchronous. Set `SECONDSHIFT_HTTP_TIMEOUT_SECONDS=300` for web and
`180` for runner, and allow at least 300 seconds through any reverse proxy.
Restart web, worker, and runner after updating their settings. Apply migrations
with the usual `python manage.py migrate` before starting the updated services.

## Connect and run

1. As a workspace owner, open **Repositories → Connect GitHub**.
2. Authorize your GitHub user. If no installation exists, SecondShift sends you
   to install the App. Select only the repositories it should read. Use
   **Manage GitHub access** to install it for another account or change repository
   access on an existing installation.
3. Back in SecondShift, choose a repository and branch using the searchable
   selectors. Its default branch is selected automatically.
4. Import it. The committed manifest and every referenced skill must be valid.
   Review job capabilities, connect a model provider and any required services,
   then enable the repository and its scheduled jobs.
5. Start a manual run and verify its report, artifacts, and delivery before
   relying on the schedule.

Only installations returned by GitHub for the authorizing user are linked to the
current workspace. A callback's installation ID is never treated as proof.
Available repositories are listed from those installations. Repository import
requires a workspace owner. Owners and admins can resync afterward.

**Resync** fetches the selected branch again. Enabled repositories with automatic
sync turned on are checked every 300 seconds by the worker. Set
`SECONDSHIFT_REPOSITORY_SYNC_INTERVAL_SECONDS` to change this interval. Set it to `0` to
disable polling. Changed capabilities continue through the existing approval
process. A failed sync preserves the last good revision and appears in repository
validation. It does not silently replace a working job definition.

## Credentials and limitations

For each sync, checkout or check, SecondShift creates one short-lived
installation token, limited to the repository and to read-only contents. Tokens
stay in trusted source-fetching processes. When a run starts, the worker sends the runner a snapshot of the
files at the run's recorded commit. The snapshot holds no Git history and no
credentials. See [repository data flow](architecture.md#repository-data-flow). GitHub's
[installation authentication guide](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation)
describes token permissions and expiry.

## Draft pull requests

Tasks can open draft pull requests when an owner allows it for a repository.
For this, the App needs two more repository permissions:

- **Contents: Read and write**, to push the `secondshift/run-<id>` branch.
- **Pull requests: Read and write**, to open the draft.

Do not grant **Workflows**. Without it, GitHub refuses any push that changes
`.github/workflows`, so an agent cannot change what CI runs. After you change
the App's permissions, each installation owner must accept them on GitHub.

Only publishing asks for a write token. It is scoped to one repository and
used for one push and one request. Syncs and checkouts stay read-only.

Disconnecting the source connection blocks future syncs and new run checkouts.
Removing repository access or uninstalling the App is detected by the next sync,
validation, or run checkout. Already running sandboxes can finish their pinned
checkout. Reinstall/re-authorize through **Connect GitHub** to reconnect.

## GitHub tools

Jobs read pull requests, commits and Actions runs through the GitHub MCP
connection. When you add it under **Connections**, choose **Use each
repository's GitHub connection**. No token is stored on it. Each tool call gets
a read-only token for the run's repository alone, from the App installation or
token the repository was added with. The token never enters the sandbox.

With the App, add these repository permissions:

- **Pull requests: Read-only**, or read and write if you also open drafts.
- **Actions: Read-only**, for CI runs and job logs.
- **Commit statuses: Read-only**.

A tool token asks only for the permissions the installation granted, and only
for read access. A permission the App lacks fails just the tools that need it.

GitHub enforces the one-repository limit for private repositories. Any token
can read a public repository, so for those SecondShift's own argument checks
keep each job on its repository. Repositories added from a local folder have no
GitHub connection to borrow. Connect GitHub tools for them with their own
fine-grained token instead.

Installing the App does not turn on GitHub tools. An owner adds the connection,
and each job's request goes through the usual approval.

## Use a personal access token

On **Repositories**, choose **Use a token** (or **Connect GitHub with a token**
on a server without the App) and paste a
[fine-grained personal access token](https://github.com/settings/personal-access-tokens/new).
SecondShift refuses classic tokens, because they reach every repository you can.

Give the token only the repositories SecondShift should use, and these
repository permissions:

- **Contents: Read-only**, to import and sync.
- **Pull requests**, **Actions** and **Commit statuses: Read-only**, for GitHub
  tools.
- **Contents** and **Pull requests: Read and write**, for draft pull requests.
  Do not grant **Workflows**, for the reason given above.

GitHub checks the token before it is saved. It is stored encrypted and never
returned to the browser or put in a sandbox. Its expiry date shows on
**Connections**, with a warning two weeks before. Paste a new token for the same
GitHub account to replace it. Its repositories keep working.

A token works differently from the App:

- One token does everything it was given. SecondShift cannot narrow it per
  step, so a token that can write is also the one used to read.
- Pushes and draft pull requests appear as the token's owner, and GitHub does
  not let you approve your own pull request.
- API calls count against the owner's hourly GitHub limit.
- When the owner loses access to a repository, or the token expires, its syncs
  and runs stop until a new token is added.

This first version uses polling, not webhooks. Each sync fetches only new
commits into a mirror kept by SecondShift. A run's files may be up to 64 MiB
compressed, 1 GiB unpacked and 200,000 files. Git LFS objects and submodule
contents are not downloaded. Repository renames require a new import so
existing capability approvals cannot silently change scope.
