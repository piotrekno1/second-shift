# Explicit CLI releases, on pinned bases. Deploy the resulting image by digest.
FROM node:24-trixie-slim@sha256:54f84b4cf8432b21aaffd5d3ce8d41e12cea0d10d33c09d2f3c9879d49d6b446 AS cli-build
# package.json names the CLI releases. package-lock.json pins every package
# they install with its checksum, so npm ci refuses a changed download. After
# changing a version, regenerate the lock in this base, from the repository root:
#   docker run --rm -v "$PWD/secondshift/agent/image:/w" -w /w <this base> \
#     npm install --package-lock-only --ignore-scripts
WORKDIR /opt/clis
COPY secondshift/agent/image/package.json secondshift/agent/image/package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force

FROM python:3.13.11-slim-trixie@sha256:2b9c9803c6a287cafa0a8c917211dddd23dcd2016f049690ee5219f5d3f1636e
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates libstdc++6 && \
    rm -rf /var/lib/apt/lists/* && mkdir -p /opt/secondshift
# Node itself, then the locked CLIs.
COPY --from=cli-build /usr/local/ /usr/local/
COPY --from=cli-build /opt/clis/ /opt/clis/
ENV PATH=/opt/clis/node_modules/.bin:$PATH
# The whole folder, so a new backend module or config file is never left out.
COPY secondshift/agent/image/ /opt/secondshift/secondshift/agent/image/
ENV PYTHONPATH=/opt/secondshift
USER 10001:10001
ENTRYPOINT ["python3", "-m", "secondshift.agent.image.agent"]
