FROM node:24-bookworm-slim AS node
FROM ghcr.io/astral-sh/uv:0.7.4 AS uv
FROM python:3.13.12-slim-bookworm
COPY --from=node /usr/local/ /usr/local/
COPY --from=uv /uv /uvx /usr/local/bin/
ARG BUILD_VERSION=dev
ARG BUILD_REVISION=unknown
LABEL org.opencontainers.image.title="a13n-docker-environment" \
      org.opencontainers.image.description="Native Docker execution environment without Envd" \
      org.opencontainers.image.source="https://github.com/converge-ai-labs/agent-foundation" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.revision="${BUILD_REVISION}" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash git curl ripgrep jq tar zip unzip ca-certificates build-essential pkg-config \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global pnpm@10.11.1 \
    && groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid sandbox --create-home sandbox \
    && mkdir -p /workspace /tmp/a13n \
    && chown sandbox:sandbox /workspace /tmp/a13n
USER sandbox
WORKDIR /workspace
# The Provider supplies init and replaces ENTRYPOINT/CMD for every container.
CMD ["python3", "-I", "-c", "import signal; signal.pause()"]
