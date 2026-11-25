---
title: Try SecondShift locally
description: Install SecondShift and run a repository skill on your machine.
---

You’ll run the documentation drift skill included in the SecondShift repository, then inspect its report. You need [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker, Git, and a model provider API key or a Claude Code or Codex subscription. Start Docker before continuing.

## Install and start

```bash
uv tool install secondshift
secondshift up
```

SecondShift opens your dashboard when it is ready. The first start builds the agent image for subscriptions and takes a few minutes. To skip it, run `secondshift up --no-agents`. Keep SecondShift running. Press Ctrl+C to stop.

## Get the example repository

In a second terminal:

```bash
git clone https://github.com/piotrekno1/second-shift.git
```

## Run the skill

1. In **Repositories**, select **Add repository**, then **Local folder**, and choose the cloned directory.
2. In **Models**, add an API key and choose a default model, or add a Claude Code or Codex subscription.
3. Open **Documentation drift** in **Jobs** and select **Run now**.
4. Open the finished run to see its report, activity, and model cost.

## Next steps

- Try more skills in [Explore](/docs/explore/), or run a
  [one-off task](/docs/tasks/), including a skill someone published on GitHub.
- Add your own scheduled job with [Repository jobs](/docs/jobs/).
- Before you share it with a team, read [Control](/docs/control/)
  and [Deploy](/docs/production-deployment/).

Model usage may incur charges. Each run stops at its
[limits](/docs/spend/#run-limits).

## Local commands

`secondshift up` opens your dashboard. With `--no-browser`, or when no browser
is available, it prints a one-time sign-in link instead:

```text
SecondShift is running at http://127.0.0.1:8000
Sign in with this link (valid for 15 minutes):
  http://127.0.0.1:8000/sign-in/<one-time-token>/

Keep this terminal open. Press Ctrl+C to stop.
Logs: secondshift logs --follow
```

```bash
secondshift status                  # Dashboard address and service health
secondshift login                   # New one-time sign-in link
secondshift logs                    # Recent service logs
secondshift logs --follow           # Stream logs until Ctrl+C
secondshift logs --service worker   # Logs for one service
secondshift up --debug              # Also print logs in the terminal
secondshift up --port 8080          # Use another dashboard port
```

Service logs live in `logs` inside the data folder printed at startup, by
default `~/.secondshift`. Each service keeps up to four files of about 2 MiB.
Sign-in links are not written to these logs.

## Upgrade

Run `uv tool upgrade secondshift`, then `secondshift up`. Read the
[release notes](https://github.com/piotrekno1/second-shift/releases) first.

Upgrading from beta 7 or earlier? Durable workflows now use their own SQLite
file, `workflows.sqlite3`, in the data folder. To keep queued runs and their
history, stop SecondShift and copy the database before you start the new
version:

```bash
cd ~/.secondshift && sqlite3 secondshift.sqlite3 ".backup workflows.sqlite3"
```

Skip this if `workflows.sqlite3` already exists. See the
[beta 8 release notes](https://github.com/piotrekno1/second-shift/blob/main/docs/releases/0.1.0b8.md).
