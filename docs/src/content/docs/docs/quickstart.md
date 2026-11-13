---
title: Try SecondShift locally
description: Install SecondShift and run a repository skill on your machine.
---

You’ll run the documentation drift check included in the SecondShift repository, then inspect its report. You need [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker, Git, and a model provider API key or a Claude Code or Codex subscription. Start Docker before continuing.

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

## Run the check

1. In **Repositories**, select **Add repository**, then **Local folder**, and choose the cloned directory.
2. In **Models**, add an API key and choose a default model, or add a Claude Code or Codex subscription.
3. Open **Documentation drift** in **Jobs** and select **Run now**.
4. Open the finished run to see its report, activity, and model cost.

## Try a skill from GitHub

To try a skill someone else published, open your repository, select **New task**, then **Skill from GitHub**, and paste a link to a `SKILL.md`, such as one from [anthropics/skills](https://github.com/anthropics/skills). SecondShift pins it to a commit and mounts it read-only. If you like the result, select **Run again** or **Schedule this skill**. See [Try a skill from GitHub](/docs/tasks/#try-a-skill-from-github).

Model usage may incur charges. For local sign-in and upgrade details, see [self-hosting](/docs/self-hosting/#local-trial). To hand over one-off work, see [Tasks](/docs/tasks/). To add your own scheduled job, continue with [Repository jobs](/docs/engineering-tasks/).
