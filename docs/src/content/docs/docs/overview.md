---
title: Documentation
description: What SecondShift is, the ideas behind it, and where to start.
tableOfContents: false
---

SecondShift runs AI agents on your repositories: on demand, on a schedule, or
when an alert fires. You describe the work as a skill in Git. SecondShift runs
it on your own server, in a sandbox without network access, and the agent can
call only the tools a workspace owner approved. It is open source under
Apache 2.0.

<video src="https://github.com/user-attachments/assets/2e48124d-489f-449b-be66-25107697e9b0" poster="/media/ci-fixer-walkthrough.jpg" controls playsinline preload="metadata" width="100%"></video>

A CI check fails on two pull requests. The CI failure fixer from Explore
traces both failures to one bug, opens a draft fix, and a second draft
schedules it.

## Where to start

<div class="doc-paths">
  <a href="/docs/evaluating/"><span class="path-number">01</span><span><strong>Evaluate it</strong><small>Lock-in, what data leaves your network, what an agent can touch, and what it can spend.</small></span><span class="path-arrow" aria-hidden="true">↗</span></a>
  <a href="/docs/quickstart/"><span class="path-number">02</span><span><strong>Try it on your laptop</strong><small>Run a skill on a repository and read its report in a few minutes. Needs Docker, uv, and a model API key or a Claude Code or Codex subscription.</small></span><span class="path-arrow" aria-hidden="true">↗</span></a>
  <a href="/docs/production-deployment/"><span class="path-number">03</span><span><strong>Deploy for your team</strong><small>Run the shared service on your own server with Docker Compose and gVisor.</small></span><span class="path-arrow" aria-hidden="true">↗</span></a>
</div>

Already running it?

- Run something once and read the report later: [one-off tasks](/docs/tasks/).
- Make it recurring: [repository jobs](/docs/jobs/).
- Give jobs access to Sentry, Linear, Grafana or your own API:
  [connections](/docs/connections/).
- Decide who approves what, and cap each run:
  [control](/docs/control/).

## How it fits together

![A run goes from its trigger through six steps: freeze the plan, open a sandbox, run the agent, save outputs, publish and notify.](/images/architecture/run-lifecycle.svg)

- A **skill** is a `SKILL.md` file of plain instructions in a repository.
  [Explore](/docs/explore/) and the [skill catalog](/docs/skill-catalog/) have
  ready-made ones.
- A **job** runs a skill when you choose: by hand, on a schedule or from a
  webhook. It lives in the repository's `.secondshift.yaml`, so changes to it
  go through review. A **task** runs a skill or written instructions once,
  without any configuration.
- A **run** is one execution. It gets a fresh sandbox with the repository at
  one commit, no network and no credentials. It stops at its
  [limits](/docs/spend/#run-limits) on time, tokens, model calls and tool
  calls.
- A **connection** gives jobs tools from a service, such as Sentry or an MCP
  server. A job asks for it by a name, its **binding**. A workspace owner
  decides which tools each job gets. The agent never holds the credential.
- A **model** is an API key or a subscription that every job in the workspace
  runs on. See [models](/docs/models/).
- A **workspace** is one team's setup: its repositories, jobs, connections,
  approvals and run history. Owners connect services and approve access,
  admins run jobs, members can view.

Comparing tools? See how SecondShift
[differs from the alternatives](/docs/comparisons/). Looking for a setting?
The **Reference** section in the sidebar has every manifest field and
environment variable.
