# syntax=docker/dockerfile:1

FROM node:24-bookworm-slim AS web-builder

WORKDIR /web
COPY apps/foundation-web/package.json apps/foundation-web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci
COPY apps/foundation-web ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/agent-envd-client/pyproject.toml packages/agent-envd-client/pyproject.toml
COPY packages/agent-environment-provider/pyproject.toml packages/agent-environment-provider/pyproject.toml
COPY packages/agent-harness/pyproject.toml packages/agent-harness/pyproject.toml
COPY packages/agent-stream-protocol/pyproject.toml packages/agent-stream-protocol/pyproject.toml
COPY packages/agent-ui/pyproject.toml packages/agent-ui/pyproject.toml
COPY packages/logging/pyproject.toml packages/logging/pyproject.toml
COPY packages/foundation-service/pyproject.toml packages/foundation-service/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package a13n-service --no-install-workspace

COPY packages/agent-envd-client packages/agent-envd-client
COPY packages/agent-environment-provider packages/agent-environment-provider
COPY packages/agent-harness packages/agent-harness
COPY packages/logging packages/logging
COPY packages/foundation-service packages/foundation-service
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package a13n-service \
    && rm -rf packages/agent-envd-client/tests \
        packages/agent-environment-provider/tests \
        packages/agent-harness/tests \
        packages/logging/tests \
        packages/foundation-service/tests

FROM python:3.13-slim-bookworm AS runtime

ARG BUILD_VERSION=dev
ARG BUILD_REVISION=unknown
LABEL org.opencontainers.image.title="agent-foundation-service" \
      org.opencontainers.image.description="Hosted agents and control services for Agent Foundation" \
      org.opencontainers.image.source="https://github.com/converge-ai-labs/agent-foundation" \
      org.opencontainers.image.revision="${BUILD_REVISION}" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tini \
    && rm -rf /var/lib/apt/lists/* \
    && test -r /etc/ssl/certs/ca-certificates.crt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv
COPY --from=web-builder --chown=app:app /web/dist /app/web
COPY --chown=app:app scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /app/var/files /app/var/objects \
    && chown -R app:app /app/var

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    FOUNDATION_HOST=0.0.0.0 \
    FOUNDATION_PORT=8000 \
    FOUNDATION_ROLE=all \
    FOUNDATION_DATABASE_BACKEND=postgresql \
    FOUNDATION_REDIS_BACKEND=redis \
    FOUNDATION_OBJECT_BACKEND=local \
    FOUNDATION_OBJECT_LOCAL_ROOT=/app/var/objects \
    FOUNDATION_FILESYSTEM_ROOT=/app/var/files \
    FOUNDATION_AUTO_MIGRATE=true \
    FOUNDATION_LOG_FORMAT=json \
    FOUNDATION_BUILD_VERSION="${BUILD_VERSION}" \
    FOUNDATION_WEB_DIST_DIR=/app/web

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ['FOUNDATION_PORT']}/healthz\", timeout=2)"]
ENTRYPOINT ["tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["foundation-service", "serve"]
