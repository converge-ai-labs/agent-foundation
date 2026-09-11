# syntax=docker/dockerfile:1
ARG SERVICE_IMAGE=a13n-service:local
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS installer
FROM ${SERVICE_IMAGE}
USER root
COPY --from=installer /usr/local/bin/uv /usr/local/bin/uv
COPY *.whl /tmp/plugin-wheels/
RUN uv pip install --python /app/.venv/bin/python --no-index --no-deps /tmp/plugin-wheels/*.whl \
    && rm -rf /tmp/plugin-wheels /usr/local/bin/uv
USER app
