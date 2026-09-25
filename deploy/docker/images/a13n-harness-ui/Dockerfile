# syntax=docker/dockerfile:1
# Context: dist/a13n-harness-ui-image, prepared by scripts/prepare_harness_ui_image.py.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY . /wheels/
RUN uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python \
        --constraint /wheels/constraints.txt /wheels/*.whl

FROM python:3.13-slim-bookworm AS runtime
ARG BUILD_VERSION=dev
ARG BUILD_REVISION=unknown
LABEL org.opencontainers.image.title="a13n-harness-ui" \
      org.opencontainers.image.description="Harness UI foreground WebUI and development workspace" \
      org.opencontainers.image.source="https://github.com/converge-ai-labs/agent-foundation" \
      org.opencontainers.image.revision="${BUILD_REVISION}" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tini git bash curl \
    && rm -rf /var/lib/apt/lists/* \
    && test -r /etc/ssl/certs/ca-certificates.crt \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app \
    && mkdir -p /home/app/.a13n-harness-ui /data /work \
    && chown -R app:app /home/app /data /work
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    A13N_HARNESS_UI_DATA_ROOT=/data \
    A13N_HARNESS_UI_BUILD_REVISION="${BUILD_REVISION}"
# Fail a release build if its image identity differs from the installed wheel.
RUN python -c 'import importlib.metadata, os; v = importlib.metadata.version("a13n-harness-ui"); expected = os.environ["BUILD_VERSION"]; assert expected == "dev" or v == expected.replace("-rc.", "rc"), (v, expected)'
USER app
WORKDIR /work
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/readyz', timeout=2)"]
ENTRYPOINT ["tini", "--"]
CMD ["a13n-harness-ui", "webui", "--host", "0.0.0.0"]
