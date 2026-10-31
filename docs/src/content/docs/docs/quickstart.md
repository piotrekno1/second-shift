---
title: Try SecondShift locally
description: Install SecondShift and run a repository skill on your machine.
---

You’ll run the documentation drift check included in the SecondShift repository, then inspect its report. You need [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker, Git, and a model provider. Start Docker before continuing.

## Install and start

```bash
release=https://github.com/piotrekno1/second-shift/releases
uv tool install --force --python 3.13 \
  "$release/download/v0.1.0b4/secondshift-0.1.0b4-py3-none-any.whl"
secondshift up
```

Open the one-time sign-in link printed in your terminal. Keep SecondShift running.

## Get the example repository

In a second terminal:

```bash
git clone https://github.com/piotrekno1/second-shift.git
```

## Run the check

1. In **Connections**, add your model provider and choose a default model.
2. In **Repositories**, select **Add local repository** and choose the cloned directory.
3. Open **Documentation drift** in **Jobs** and select **Run now**.
4. Open the finished run to see its report, activity, and model cost.

Model usage may incur charges. For local sign-in and upgrade details, see [self-hosting](/docs/self-hosting/#local-trial). To add your own task, continue with [Add a task](/docs/engineering-tasks/).
