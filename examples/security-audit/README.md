# Weekly security audit

Run Cloudflare's [security-audit skill](https://github.com/cloudflare/security-audit-skill)
on a schedule. It maps the codebase's trust boundaries, hunts for
vulnerabilities, and has each candidate checked by a fresh agent that never
saw the hunt. Only candidates that survive that check become confirmed
findings.

A full audit is too large for one run, so this example audits one part of the
codebase per run. Each run saves the audit's coverage ledger and findings in
`/outputs`. The next run reads them from `/history`, takes the next part, and
re-checks earlier findings against the current code.

The [wrapper skill](.agents/skills/scheduled-security-audit/SKILL.md) adapts
the audit to a SecondShift run. Cloudflare's skill is not included. You copy
it into your repository yourself.

It complements the [security regression review](../security-regression/README.md),
which looks at the last week's changes. This one works through the whole
codebase over several runs.

## Verified findings need sub-agents

The audit counts a finding as confirmed only after a separate agent verifies
it. The job sets `subagents: 3`, so each run can start three verifiers.

- With the built-in model agent or a Claude Code subscription, the run
  verifies candidates with sub-agents, highest priority first. Candidates left
  over when they run out stay unverified, and the run is marked incomplete.
- Codex runs cannot start sub-agents yet, and neither can a job with
  `subagents: 0`. The audit still runs, but it reports candidates as
  unverified leads with no severity, and marks the run incomplete.

Sub-agents use the run's tools and count toward its limits. They use the same
model as the run, so their check is a fresh look, not a second opinion from
another model. See [sub-agents](../../docs/repository-configuration.md#sub-agents).

## Try it

1. Copy Cloudflare's skill into your repository at
   `.agents/skills/security-audit/`. Its `SKILL.md` must end up at
   `.agents/skills/security-audit/SKILL.md`:

   ```bash
   git clone --depth 1 https://github.com/cloudflare/security-audit-skill /tmp/security-audit-skill
   mkdir -p .agents/skills
   cp -R /tmp/security-audit-skill/skills/security-audit .agents/skills/security-audit
   ```

   Its MIT license comes with it. Pin the commit you copied and review
   updates before you take them.
2. Copy this directory's `.agents/skills/scheduled-security-audit` and
   `.secondshift.yaml` into your repository root. Merge the job into an
   existing manifest if necessary. Commit everything.
3. Connect a model provider or an agent subscription, and import or resync the
   repository in SecondShift.
4. Raise this job's run limits on its page. An audit run needs far more time,
   turns and tool calls than the defaults of 15 minutes, 24 turns and 150
   tool calls. Start with an hour, 150 turns and 400 tool calls, and adjust
   from the first runs. Values above the server's maximums need the
   `_CEILING` settings in [run limits](../../docs/self-hosting.md#run-limits).
5. Choose **Run now**. The first run maps the codebase and audits the
   highest-priority part. Read its report and saved files, then enable the
   Monday 06:00 Europe/Warsaw schedule.

The job needs no connections and no approvals. It only reads the repository
and writes its own files.

## What to expect

- Each report starts with the run's status and whether its findings were
  independently verified. Confirmed findings come with severity, evidence
  and a suggested fix. Records that need facts outside the source are listed
  separately, with no severity.
- The saved files follow the audit skill's formats: `findings.json`,
  `coverage-ledger.json`, `architecture.md` and the report files.
- The run does not execute the target's code. Behavior that only running
  code could show is reported as needing validation.
- The audit's validators need Node, which SecondShift's default sandbox image
  does not include. Without it, the run checks its files by reading them and
  says the validators did not run. To run them, set
  `SECONDSHIFT_RUNNER_IMAGE` to an image with Node.
- Run history keeps the last successful run's files. A failed run is skipped,
  so the next run continues from the last good ledger. Reports and files are
  kept for `SECONDSHIFT_RUN_RETENTION_DAYS`, 90 by default.
- No run certifies the codebase as secure. The ledger shows what has been
  covered so far, and what has not.

This sample is not included in SecondShift's root manifest. Adding the files
activates no job and grants no access.
