# Weekly security regression review

Review recent changes for concrete security defects and suggest fixes and
regression tests. The skill reads source, tests, dependency lockfiles, and
deployment configuration. Optional GitHub access provides change evidence.
Optional observability access provides runtime context through Sentry, Grafana,
Datadog, Splunk, or another approved connection. It does not run Strix or Striga.

The [security regression skill](.agents/skills/security-regression/SKILL.md)
lives in this example. Root `.agents/skills/security-regression` symlinks here
so coding agents discover the same skill we ship for users to copy.

## Try the proposal

1. Copy this directory's `.agents/skills/security-regression` and
   `.secondshift.yaml` into your repository root. Merge the job into an existing
   manifest if necessary. Commit both files. To try it in this repository, merge
   the sample job into the root manifest and set its skill path to
   `examples/security-regression/.agents/skills/security-regression/SKILL.md`.
2. Connect a model provider, choose its default model, and import or resync the
   repository in SecondShift.
3. Optionally connect GitHub with binding `github` and approve `list_commits`
   and `get_commit` for this job. See the
   [engineering briefing setup](../engineering-briefing/README.md) for token and
   repository requirements. This is recommended for regression reviews: the
   sandbox contains no Git history. Without usable change evidence, the report
   explicitly falls back to a bounded snapshot review.
4. Optionally connect observability tools. The manifest includes Sentry as a
   ready-made example with binding `sentry`. For Grafana, Datadog, Splunk, or
   another service, replace or supplement that entry with a logical binding
   and the actual read operation names from your approved MCP or API connection.
   Set `required: false` for optional context. Choose operations that expose
   relevant logs, traces, metrics, deployment markers, or security events.
   Specify the intended service and environment. Missing observability does
   not block a code review, and declaring a connection does not grant access.
5. Choose **Run now** and review its findings and coverage before enabling the
   Monday 09:00 Europe/Warsaw schedule. Remove the schedule for manual use only.

This sample is proposed for review and is not included in SecondShift's root
manifest. The example directory is a template, not an independent Git repository.
No job or service connection is activated by adding these files.

## Scope and useful additions

The default window is seven days, not changes since the last successful run.
There is no persistent findings baseline or automatic deduplication. Live GitHub
can differ from the synchronized revision. The report must disclose comparison
limits. Observability is supporting evidence, not proof that code is secure.

Useful repository context includes an architecture or threat model, intended
roles and tenant boundaries, security-sensitive configuration, dependency
lockfiles, and focused tests. Release-to-commit metadata makes observability
evidence much more useful. Advisory feeds, prior findings, or a staging scanner
could be added through separately approved connections in a later integration.
The sample does not assume they exist.

Dependencies for offline checks must already be in the server-owned sandbox
image. The skill does not install packages, probe deployed applications, modify
code, or open issues. Reports distinguish confirmed defects from candidates
needing validation and list the checks that could not run.

To deliver reports, add a webhook connection and a job `notify` entry, then
approve notification access. See
[repository configuration](../../docs/repository-configuration.md) for bindings,
approval, and delivery details.
