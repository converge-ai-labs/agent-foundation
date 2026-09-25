# syntax=docker/dockerfile:1

# The Console is static browser code, so it builds once on the build platform for every image platform.
FROM --platform=$BUILDPLATFORM node:24-bookworm-slim AS console
WORKDIR /src/frontend
COPY frontend/package.json ./
RUN --mount=type=cache,id=a13n-console-npm,target=/root/.npm \
    npm install --global --no-audit --no-fund "$(node -p 'require("./package.json").packageManager')"

# Only these workspace packages are needed by Console. Keep source changes out
# of the dependency layer and retain downloaded packages when manifests change.
COPY frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
COPY frontend/apps/a13n-console/package.json ./apps/a13n-console/package.json
COPY frontend/packages/a13n-ui/package.json ./packages/a13n-ui/package.json
RUN --mount=type=cache,id=a13n-console-pnpm,target=/pnpm/store \
    pnpm install --frozen-lockfile --store-dir=/pnpm/store --reporter=append-only

COPY frontend/tsconfig.base.json ./
COPY frontend/apps/a13n-console/ ./apps/a13n-console/
COPY frontend/packages/a13n-ui/ ./packages/a13n-ui/
COPY proto/a13n-service/openapi.json /src/proto/a13n-service/openapi.json
RUN pnpm --filter a13n-console build

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/a13n-envd-client/pyproject.toml packages/a13n-envd-client/pyproject.toml
COPY packages/a13n-harness/pyproject.toml packages/a13n-harness/pyproject.toml
COPY packages/a13n-stream-protocol/pyproject.toml packages/a13n-stream-protocol/pyproject.toml
COPY packages/a13n-harness-ui/pyproject.toml packages/a13n-harness-ui/pyproject.toml
COPY packages/a13n-logging/pyproject.toml packages/a13n-logging/pyproject.toml
COPY packages/a13n-service/pyproject.toml packages/a13n-service/pyproject.toml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package a13n-service --no-install-workspace

COPY packages/a13n-envd-client packages/a13n-envd-client
COPY packages/a13n-harness packages/a13n-harness
COPY packages/a13n-stream-protocol packages/a13n-stream-protocol
COPY packages/a13n-logging packages/a13n-logging
COPY packages/a13n-service packages/a13n-service
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --package a13n-service \
    && rm -rf packages/a13n-envd-client/tests \
        packages/a13n-harness/tests \
        packages/a13n-stream-protocol/tests \
        packages/a13n-logging/tests \
        packages/a13n-service/tests
COPY --from=console /src/frontend/apps/a13n-console/dist packages/a13n-service/a13n_service/static

FROM python:3.13-slim-bookworm AS runtime

ARG BUILD_VERSION=dev
ARG BUILD_REVISION=unknown
LABEL org.opencontainers.image.title="a13n-service" \
      org.opencontainers.image.description="Hosted agents, control services and Console for Agent Foundation" \
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
COPY --chown=root:root deploy/docker/images/a13n-service/service.toml /app/service.toml
COPY --chown=root:root --chmod=755 deploy/docker/images/a13n-service/entrypoint.sh /usr/local/bin/a13n-entrypoint
RUN chmod -R go-w /app \
    && mkdir -p /app/var/objects \
    && chown -R app:app /app/var

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

# BUILD_VERSION labels the artifact; the package itself owns its runtime version.
RUN python -c 'import importlib.metadata, os; v = importlib.metadata.version("a13n-service"); expected = os.environ["BUILD_VERSION"]; assert expected == "dev" or v == expected.replace("-rc.", "rc"), (v, expected)'

USER app
EXPOSE 8000
# The server answers only after startup, including an `all`/`control` automatic migration.
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --start-interval=2s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5)"]
ENTRYPOINT ["a13n-entrypoint"]
CMD ["a13n-service", "--config", "/app/service.toml", "run"]
