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
COPY --from=builder --chown=root:root /app /app
COPY --chown=root:root scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY --chown=root:root deploy/containers/a13n-service/service.toml /app/service.toml
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && chmod -R go-w /app \
    && chmod -R a-w /app/packages/a13n-service/a13n_service/agent_configuration/assets \
    && mkdir -p /app/var/files /app/var/objects \
    && chown -R app:app /app/var

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# BUILD_VERSION labels the artifact; the package itself owns its runtime version.
RUN python -c 'import importlib.metadata, os; v = importlib.metadata.version("a13n-service"); expected = os.environ["BUILD_VERSION"]; assert expected == "dev" or v == expected.replace("-rc.", "rc"), (v, expected)'

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD ["a13n-service", "--config", "/app/service.toml", "config", "healthcheck"]
ENTRYPOINT ["tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["a13n-service", "--config", "/app/service.toml", "serve"]
