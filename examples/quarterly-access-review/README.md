# Quarterly access review

Use the [skill](.agents/skills/quarterly-access-review/SKILL.md) to find accounts
and permissions that owners should change, remove, or confirm. It compares
workforce status and access rules with identity-provider and application data.
It proposes decisions without changing access.

## Expected output

| Output | What it tells the reviewer |
| --- | --- |
| Confirmed mismatches | Active access for a verified departed person, an expired grant, or another supported mismatch against an explicit rule. |
| Needs confirmation | Access whose purpose, owner, identity mapping, or exception cannot be established. |
| Proposed decisions | The specific permission to change, why, the supporting evidence, and the owner who should decide. |
| Coverage ledger | Every expected system, what was inspected, collection dates, incomplete data, and systems not reviewed. |

Inactivity alone does not justify removal. Service accounts, emergency accounts,
leave, contractors, and approved exceptions need their own policy treatment.
Missing access to a system is reported as unreviewed, not compliant.

The review labels the previous completed quarter, but inspects current access
at collection time. It cannot reconstruct the quarter's full access history
without historical records. It does not capture owner approval or complete a
formal certification process.

## Connections and scope

The sample manifest is a setup template. Add the actual read operations under
`requires.mcps` or `requires.apis` with `required: true` for essential sources.
Tool names vary by service, so the template does not invent them.

| Source | Data the approved connection should expose |
| --- | --- |
| Workforce directory | Verified identities, employee and contractor status, teams, managers, and end dates. |
| Identity provider | Account state, groups, application assignments, privileged roles, and identity links. |
| Individual systems | Actual accounts, roles, memberships, inherited permissions, guests, and non-human accounts where supported. |
| Access policy and approvals | Expected access by role, system ownership, temporary grant expiry, and approved exceptions. These can be committed guidance or approved service records. |

Start with the directory, identity provider, and a bounded set of important
systems. Add direct system connections as coverage grows. Identity-provider
assignments alone can miss local accounts and grants inside applications. The
number of MCP connections depends on where the data lives, and some APIs or
MCP tools may expose only part of a system's permission model.

Provide an explicit inventory and review policy in the skill or a linked
repository document. Identify workforce and system scope, authoritative identity
sources, system owners, role rules, privileged access, temporary grants, and
exception handling. Keep endpoints, credentials, and secrets in workspace
connections. Never commit personnel exports or access inventories to the repo.

## Try it

1. Copy this example's actual `.agents/skills/quarterly-access-review` into your
   repository's `.agents/skills` and merge its manifest into the root manifest.
2. Add the scope and policy guidance and actual required read operations.
   Commit the configuration and skill.
3. Connect a model provider and the approved data sources, import or resync,
   then approve this job's access. The skill requires no write operations.
4. Choose **Run now**. Check a known departed account, a valid exception, an
   ambiguous identity, and a system the connection cannot inspect. The last
   case must appear in the coverage ledger as not reviewed or partial.

**Begin with a manual run, check its results and tool activity, then enable a
schedule if useful.** The sample proposes 09:00 Europe/Warsaw on January 1,
April 1, July 1, and October 1. Adjust the timing to your review process.

Large inventories may exceed a single run's collection or context budget. Use
separate jobs for bounded teams or system groups with explicit inventories and
the same review date. Each job must report its own coverage. Do not claim a
consolidated organization review unless the results have actually been combined.

Reports contain sensitive access information. Configure any webhook delivery
for the intended reviewers. Avoid broad Slack channels for account-level
findings. The skill does not message people or open tickets itself. See
[task setup](../../docs/engineering-tasks.md) and
[notification delivery](../../docs/repository-configuration.md#deliver-reports-to-a-webhook).

## Proposal status

This example is not registered in the root manifest and has not been trialed
against live organization data. Root `.agents/skills/quarterly-access-review`
is a discovery symlink to the canonical skill here. A separate remediation
workflow would need explicit action approval and fresh evidence before writes.
