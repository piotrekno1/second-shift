# GitHub App repository access

SecondShift uses a GitHub App to read repositories and synchronize committed
`.secondshift.yaml` files and skills. This is separate from GitHub sign-in and
from the GitHub MCP connection used by jobs.

## Register the App

Register an App under your GitHub account or organization in **Settings →
Developer settings → GitHub Apps → New GitHub App**.

- Homepage URL: your public SecondShift HTTPS address.
- Callback URL: `https://secondshift.example.com/-/github/callback/`.
- Enable **Request user authorization (OAuth) during installation**.
- Leave the setup URL empty. Installation uses the authorization callback.
- Disable webhooks for now. The worker polls for changes instead.
- Repository permissions: **Contents: Read-only**. GitHub includes metadata read
  access automatically. No organization or account permissions are needed.
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
3. Back in SecondShift, choose a repository and optionally a branch. Leaving the
   branch empty uses its default branch at import time.
4. Import it. The committed manifest and every referenced skill must be valid.
   Review job capabilities, connect a model provider and any required services,
   then enable the repository and its scheduled jobs.
5. Start a manual run and verify its report, artifacts, and delivery before
   relying on the schedule.

Only installations returned by GitHub for the authorizing user are linked to the
current workspace; a callback's installation ID is never treated as proof.
Available repositories are listed from those installations. Repository import
requires a workspace owner; owners and admins can resync afterward.

**Resync** fetches the selected branch again. Enabled repositories with automatic
sync turned on are checked every 300 seconds by the worker. Set
`SECONDSHIFT_REPOSITORY_SYNC_INTERVAL_SECONDS` to change this interval; `0`
disables polling. Changed capabilities continue through the existing approval
process. A failed sync preserves the last good revision and appears in repository
validation. It does not silently replace a working job definition.

## Credentials and limitations

SecondShift creates short-lived installation tokens scoped to the repository
and read-only contents permission. Tokens stay in trusted source-fetching
processes. The runner receives a credential-free Git bundle; sandboxes receive
only the checkout at the run's recorded commit. GitHub's
[installation authentication guide](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation)
describes token permissions and expiry.

Disconnecting the source connection blocks future syncs and new run checkouts.
Removing repository access or uninstalling the App is detected by the next sync,
validation, or run checkout. Already running sandboxes can finish their pinned
checkout. Reinstall/re-authorize through **Connect GitHub** to reconnect.
The GitHub App does not automatically authorize a job's GitHub MCP tools.

This first version uses polling, not webhooks. It fetches the full selected branch
history on each changed revision and accepts bundles up to 64 MiB. Git LFS objects
and submodule contents are not downloaded. Repository renames require a new
import so existing capability approvals cannot silently change scope. Back up
the runner repository store alongside its database; restoring only the control
plane database does not restore the pinned Git objects.
