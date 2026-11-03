# Explicit CLI releases, on pinned bases. Deploy the resulting image by digest.
FROM node:24-trixie-slim@sha256:54f84b4cf8432b21aaffd5d3ce8d41e12cea0d10d33c09d2f3c9879d49d6b446 AS cli-build
ARG CLAUDE_CODE_VERSION=2.1.316
ARG CODEX_VERSION=0.166.0
RUN npm install -g "@anthropic-ai/claude-code@$CLAUDE_CODE_VERSION" "@openai/codex@$CODEX_VERSION" && \
    npm cache clean --force

FROM python:3.13.11-slim-trixie@sha256:2b9c9803c6a287cafa0a8c917211dddd23dcd2016f049690ee5219f5d3f1636e
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libstdc++6 && \
    rm -rf /var/lib/apt/lists/* && mkdir -p /opt/secondshift
COPY --from=cli-build /usr/local/ /usr/local/
COPY secondshift/runner/agent_image/agent.py secondshift/runner/agent_image/outcomes.py /opt/secondshift/secondshift/runner/agent_image/
ENV PYTHONPATH=/opt/secondshift
USER 10001:10001
ENTRYPOINT ["python3", "-m", "secondshift.runner.agent_image.agent"]
