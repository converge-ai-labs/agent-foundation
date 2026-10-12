# syntax=docker/dockerfile:1

# Local builds compile this checkout; CI publications select same-run artifacts.
ARG ENVD_BINARY_SOURCE=source
ARG SANDBOX_VARIANT=standard

FROM rust:1.96-trixie AS builder

WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY crates/a13n-envd crates/a13n-envd
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    cargo build --release --locked --package a13n-envd

FROM scratch AS envd-source
COPY --from=builder /src/target/release/a13n-envd /a13n-envd

FROM scratch AS envd-prebuilt
ARG TARGETARCH
COPY --chmod=0755 tmp/sandbox-binaries/${TARGETARCH}/a13n-envd /a13n-envd

# BuildKit skips the Rust builder entirely when prebuilt is selected.
FROM envd-${ENVD_BINARY_SOURCE} AS envd

FROM node:24-trixie-slim AS node
FROM ghcr.io/astral-sh/uv:0.7.4 AS uv
FROM debian:trixie-slim AS slim-runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgcc-s1 tini \
    && rm -rf /var/lib/apt/lists/*
ENTRYPOINT ["tini", "--"]

FROM python:3.13.12-slim-trixie AS standard-runtime

COPY --from=node /usr/local/ /usr/local/
COPY --from=uv /uv /uvx /usr/local/bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash build-essential ca-certificates chromium curl fonts-noto-cjk fonts-noto-color-emoji \
        git iproute2 jq libc-bin nftables openssh-client patch pkg-config procps ripgrep sudo \
        tini unzip util-linux zip \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global pnpm@10.11.1 agent-browser@0.39.0 \
    && npm cache clean --force \
    && install -d /usr/local/share/a13n/skills \
    && cp -R /usr/local/lib/node_modules/agent-browser/skills/agent-browser /usr/local/share/a13n/skills/ \
    && printf 'sandbox ALL=(ALL:ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/sandbox \
    && chmod 0440 /etc/sudoers.d/sandbox \
    && visudo -cf /etc/sudoers

# System Chromium is available on both published architectures; startup is offline.
ENV AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium
COPY --chmod=0755 deploy/docker/images/sandbox/init-skills.sh /usr/local/bin/a13n-sandbox-init
COPY --chmod=0755 deploy/docker/images/sandbox/entrypoint.sh /usr/local/bin/a13n-sandbox-entrypoint
ENTRYPOINT ["tini", "--", "a13n-sandbox-entrypoint"]

# Both variants use identical daemon bytes, identity and working-directory defaults.
FROM ${SANDBOX_VARIANT}-runtime AS runtime
RUN groupadd --gid 1000 sandbox \
    && useradd --uid 1000 --gid sandbox --create-home --shell /bin/bash sandbox \
    && install -d -o sandbox -g sandbox /workspace /tmp/a13n \
    && install -d -m 0700 /var/lib/a13n-envd /run/a13n-envd-state

COPY --from=envd /a13n-envd /usr/local/bin/a13n-envd
COPY LICENSE /usr/share/licenses/a13n-envd/LICENSE

# Source identity must not invalidate the stable toolchain installation layer.
ARG BUILD_VERSION=dev
ARG BUILD_REVISION=unknown
ARG SANDBOX_VARIANT
LABEL ai.a13n.environment.user="sandbox" \
      ai.a13n.sandbox.variant="${SANDBOX_VARIANT}" \
      org.opencontainers.image.title="a13n-sandbox" \
      org.opencontainers.image.description="Agent Foundation ${SANDBOX_VARIANT} execution image" \
      org.opencontainers.image.source="https://github.com/converge-ai-labs/agent-foundation" \
      org.opencontainers.image.revision="${BUILD_REVISION}" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0"

# A release label must agree with the version compiled into the daemon.
RUN test "$BUILD_VERSION" = dev || test "$(a13n-envd --version)" = "a13n-envd $BUILD_VERSION"

# The daemon retains root; Session file operations and commands switch accounts.
# The standard variant also supplies passwordless sudo for development tasks.
USER root
WORKDIR /workspace
ENV A13N_ENVD_EXECUTION_UID=1000 \
    A13N_ENVD_EXECUTION_GID=1000 \
    A13N_ENVD_FULL_CONTROL=true \
    A13N_ENVD_STATE_DIR=/var/lib/a13n-envd \
    A13N_ENVD_RUNTIME_DIR=/run/a13n-envd-state
# Egress remains opt-in: the outer runtime must also grant namespace permissions.
CMD ["a13n-envd"]
