# syntax=docker/dockerfile:1
ARG SANDBOX_IMAGE=a13n-sandbox:local
FROM ${SANDBOX_IMAGE} AS daemon
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/a13n-envd-client/pyproject.toml packages/a13n-envd-client/pyproject.toml
COPY packages/a13n-environment/pyproject.toml packages/a13n-environment/pyproject.toml
COPY packages/a13n-harness/pyproject.toml packages/a13n-harness/pyproject.toml
COPY packages/a13n-stream-protocol/pyproject.toml packages/a13n-stream-protocol/pyproject.toml
COPY packages/a13n-harness-ui/pyproject.toml packages/a13n-harness-ui/pyproject.toml
COPY packages/a13n-logging/pyproject.toml packages/a13n-logging/pyproject.toml
COPY packages/a13n-service/pyproject.toml packages/a13n-service/pyproject.toml
COPY packages/a13n-envd-client packages/a13n-envd-client
COPY packages/a13n-environment packages/a13n-environment
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --package a13n-environment
COPY --from=daemon /usr/local/bin/a13n-envd /usr/local/bin/a13n-envd
COPY dev/live_tests/file_resource_worker.py /app/file_resource_worker.py
RUN groupadd --gid 10001 fixture \
    && useradd --uid 10001 --gid fixture --create-home --home-dir /home/sandbox fixture \
    && mkdir /workspace && chown fixture:fixture /workspace
USER fixture
ENV PATH="/app/.venv/bin:${PATH}" PYTHONUNBUFFERED=1

FROM runtime AS docker-sandbox
ENTRYPOINT []
CMD ["a13n-envd"]

FROM runtime AS worker
ENTRYPOINT ["python", "/app/file_resource_worker.py"]
