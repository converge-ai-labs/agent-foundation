# syntax=docker/dockerfile:1

FROM rust:1.96-bookworm AS builder

WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY crates/a13n-envd crates/a13n-envd
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    cargo build --release --locked --package a13n-envd

FROM node:24-bookworm-slim AS node
FROM ghcr.io/astral-sh/uv:0.7.4 AS uv
FROM python:3.13.12-slim-bookworm AS runtime

COPY --from=node /usr/local/ /usr/local/
COPY --from=uv /uv /uvx /usr/local/bin/

ARG BUILD_VERSION=dev
ARG BUILD_REVISION=unknown
LABEL ai.a13n.environment.user="sandbox" \
      org.opencontainers.image.title="a13n-sandbox" \
      org.opencontainers.image.description="Agent Foundation development sandbox for native Docker and Envd" \
      org.opencontainers.image.source="https://github.com/converge-ai-labs/agent-foundation" \
      org.opencontainers.image.revision="${BUILD_REVISION}" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash build-essential ca-certificates curl git iproute2 jq libc-bin nftables openssh-client patch \
        pkg-config procps ripgrep sudo tini unzip util-linux zip \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global pnpm@10.11.1 \
    && groupadd --gid 1000 sandbox \
    && useradd --uid 1000 --gid sandbox --create-home --shell /bin/bash sandbox \
    && install -d -o sandbox -g sandbox /workspace /tmp/a13n \
    && install -d -m 0700 /var/lib/a13n-envd /run/a13n-envd-state \
    && printf 'sandbox ALL=(ALL:ALL) NOPASSWD: ALL\n' > /etc/sudoers.d/sandbox \
    && chmod 0440 /etc/sudoers.d/sandbox \
    && visudo -cf /etc/sudoers

COPY --from=builder /src/target/release/a13n-envd /usr/local/bin/a13n-envd
COPY LICENSE /usr/share/licenses/a13n-envd/LICENSE
# A release label must agree with the version compiled into the daemon.
RUN test "$BUILD_VERSION" = dev || test "$(a13n-envd --version)" = "a13n-envd $BUILD_VERSION"

# The daemon retains root; Session file operations and commands switch accounts.
# The sandbox account can explicitly sudo for everyday development tasks.
USER root
WORKDIR /workspace
ENV A13N_ENVD_EXECUTION_UID=1000 \
    A13N_ENVD_EXECUTION_GID=1000 \
    A13N_ENVD_FULL_CONTROL=true \
    A13N_ENVD_STATE_DIR=/var/lib/a13n-envd \
    A13N_ENVD_RUNTIME_DIR=/run/a13n-envd-state
# Egress remains opt-in: the outer runtime must also grant namespace permissions.
ENTRYPOINT ["tini", "--"]
CMD ["a13n-envd"]
