---
title: Try SecondShift locally
description: Install SecondShift and run a repository skill on your machine.
---

You’ll run the documentation drift check included in the SecondShift repository, then inspect its report. You need [uv](https://docs.astral.sh/uv/getting-started/installation/), Docker, Git, and a model provider. Start Docker before continuing.

## Install and start

```bash
release=https://github.com/piotrekno1/second-shift/releases
uv tool install --force --python 3.13 \
  "$release/download/v0.1.0b7/secondshift-0.1.0b7-py3-none-any.whl"
secondshift up
```

SecondShift opens your dashboard when all services are ready. If the browser
cannot open, use the one-time sign-in link printed in your terminal. Keep
SecondShift running; press Ctrl+C to stop it.

Useful local commands:

```bash
secondshift status                  # Dashboard address and service health
secondshift login                   # A fresh one-time sign-in link
secondshift logs                    # Recent service diagnostics
secondshift logs --follow           # Stream diagnostics until Ctrl+C
secondshift logs --service worker   # Inspect one service
secondshift up --debug              # Also stream diagnostics while running
```

Service logs are saved under `logs` in the data folder printed at startup.
Each service retains up to four files of approximately 2 MiB each. Sign-in links
printed by the launcher are excluded from these logs. Use `--no-browser` to
print a sign-in link without opening your browser, or `--port 8001` to use a
different dashboard port.

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
