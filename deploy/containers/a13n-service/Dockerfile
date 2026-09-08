# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/a13n-envd-client/pyproject.toml packages/a13n-envd-client/pyproject.toml
COPY packages/a13n-environment/pyproject.toml packages/a13n-environment/pyproject.toml
COPY packages/a13n-harness/pyproject.toml packages/a13n-harness/pyproject.toml
COPY packages/a13n-stream-protocol/pyproject.toml packages/a13n-stream-protocol/pyproject.toml
COPY packages/a13n-harness-ui/pyproject.toml packages/a13n-harness-ui/pyproject.toml
COPY packages/a13n-logging/pyproject.toml packages/a13n-logging/pyproject.toml
COPY packages/a13n-service/pyproject.toml packages/a13n-service/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package a13n-service --no-install-workspace

COPY packages/a13n-envd-client packages/a13n-envd-client
COPY packages/a13n-environment packages/a13n-environment
COPY packages/a13n-harness packages/a13n-harness
COPY packages/a13n-stream-protocol packages/a13n-stream-protocol
COPY packages/a13n-logging packages/a13n-logging
COPY packages/a13n-service packages/a13n-service
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package a13n-service \
    && rm -rf packages/a13n-envd-client/tests \
        packages/a13n-environment/tests \
        packages/a13n-harness/tests \
        packages/a13n-stream-protocol/tests \
        packages/a13n-logging/tests \
        packages/a13n-service/tests

FROM python:3.13-slim-bookworm AS runtime

ARG BUILD_VERSION=dev
ARG BUILD_REVISION=unknown
LABEL org.opencontainers.image.title="a13n-service" \
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
COPY --chown=app:app scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /app/var/files /app/var/objects \
    && chown -R app:app /app/var

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    A13N_SERVICE_HOST=0.0.0.0 \
    A13N_SERVICE_PORT=8000 \
    A13N_SERVICE_ROLE=all \
    A13N_SERVICE_DATABASE_BACKEND=postgresql \
    A13N_SERVICE_REDIS_BACKEND=redis \
    A13N_SERVICE_OBJECT_BACKEND=local \
    A13N_SERVICE_OBJECT_LOCAL_ROOT=/app/var/objects \
    A13N_SERVICE_FILESYSTEM_ROOT=/app/var/files \
    A13N_SERVICE_AUTO_MIGRATE=true \
    A13N_SERVICE_LOG_FORMAT=json \
    A13N_SERVICE_BUILD_VERSION="${BUILD_VERSION}"

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ['A13N_SERVICE_PORT']}/healthz\", timeout=2)"]
ENTRYPOINT ["tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["a13n-service", "serve"]
