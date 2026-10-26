# Upcoming on-call coverage

Proposal for review. The [skill](.agents/skills/on-call-coverage/SKILL.md) checks
the next 14 days of on-call assignments against explicit time off and shared
leave calendars. It reports gaps and conflicts for the team to resolve, without
changing schedules or calendars.

The sample proposes a daily check at 08:00 Europe/Warsaw. The first run should
verify calendar access, identity matching, and the team's coverage rules before
any schedule is enabled. This job is not registered in the root manifest.

## What counts as a finding

| Finding | What establishes it | Suggested follow-up |
| --- | --- | --- |
| Coverage gap | Complete schedule data shows fewer assigned responders than required for a role and interval. | Arrange coverage for that interval. |
| Time-off conflict | A resolved assignment overlaps explicit out-of-office or approved leave. | Check backup coverage and arrange an override if needed. |
| Needs confirmation | Only a busy block, tentative leave, an uncertain identity, or incomplete data is available. | Confirm availability or fix missing access. |

A meeting is not automatically an on-call conflict. An empty calendar does not
prove someone is available. The skill reports the conflicting interval without
revealing personal event titles or reasons for leave.

## Connections and policy

The sample manifest is incomplete until you declare the actual operations from
your approved connections under `requires.mcps` or `requires.apis`, with
`required: true`. It intentionally does not guess provider-specific tool names.

| Source | Operations the connection needs to expose |
| --- | --- |
| PagerDuty, incident.io, or another on-call service | Read schedules, resolved assignments including overrides, and responder identities. Read escalation or backup rules if used by the coverage policy. |
| Google Calendar or another calendar service | Read explicit out-of-office events or an approved leave calendar for a time range, including recurring instances and cancellations. Free/busy is sufficient only for possible conflicts. |

An API feature is not necessarily exposed by every MCP connector. Check the
selected connection's tool definitions. Use an approved HTTP API connection
when the required operation is not exposed through MCP.

Provide task guidance in the skill or a linked repository document that defines:

- The teams, schedules, services, and roles in scope.
- Required coverage hours, time zones, and minimum staffing for each role.
- Whether and when backup or escalation coverage satisfies a requirement.
- The responder-to-calendar mapping, using verified identifiers.
- Which leave calendars represent approved unavailability and how to handle
  pending leave. A shared calendar must identify the affected responder.

Keep endpoints, credential references, and secrets in workspace connections.
Access to an on-call service does not grant access to responders' calendars.
For personal calendars, use explicitly authorized access or calendar sharing.
Prefer a shared leave calendar or out-of-office-only query over broad access
to personal event details.

## Try the proposal

1. Copy this example's actual `.agents/skills/on-call-coverage` into your
   repository's `.agents/skills` and merge the sample job into the root manifest.
2. Add the team policy and exact required read operations, then commit the files.
3. Connect a model provider, configure the service connections, import or resync,
   and approve the job's requested access.
4. Choose **Run now**. Check a known override, a known leave overlap, and a
   responder whose calendar is inaccessible. Verify the last case is reported
   as unknown rather than free.

**Begin with a manual run, check its results and tool activity, then enable a
schedule if useful.** The skill has not been trialed against live team data.

To deliver the brief to Slack, add a webhook connection and a job `notify`
binding, then approve notification access. SecondShift delivers the run summary
and a link to the report. Use a channel whose audience may see responder names
and availability intervals. The skill does not message individuals directly.
See [notification setup](../../docs/repository-configuration.md#deliver-reports-to-a-webhook).

## API references

- [PagerDuty schedules](https://docs.pagerduty.com/developer/api/reference/rest/schedules/list-schedules)
- [incident.io schedule entries](https://docs.incident.io/api-reference/schedule-entries-v2/list)
- [Google Calendar event queries](https://developers.google.com/workspace/calendar/api/v3/reference/events/list)
- [Google Calendar free/busy queries](https://developers.google.com/workspace/calendar/api/v3/reference/freebusy/query)
- [Google Calendar sharing](https://developers.google.com/workspace/calendar/api/concepts/sharing)

The canonical skill lives here. Root `.agents/skills/on-call-coverage` is a
discovery symlink to the same file.
