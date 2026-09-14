# syntax=docker/dockerfile:1

FROM rust:1.96-bookworm AS builder

WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY crates/a13n-envd crates/a13n-envd
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    cargo build --release --locked --package a13n-envd

FROM debian:bookworm-slim AS runtime

ARG BUILD_VERSION=dev
ARG BUILD_REVISION=unknown
LABEL org.opencontainers.image.title="a13n-sandbox" \
      org.opencontainers.image.description="Agent Foundation sandbox with a13n-envd" \
      org.opencontainers.image.source="https://github.com/converge-ai-labs/agent-foundation" \
      org.opencontainers.image.revision="${BUILD_REVISION}" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0"

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl git procps tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid sandbox --create-home sandbox \
    && mkdir -p /workspace \
    && chown sandbox:sandbox /workspace

COPY --from=builder /src/target/release/a13n-envd /usr/local/bin/a13n-envd
COPY LICENSE /usr/share/licenses/a13n-envd/LICENSE
# A release label must agree with the version compiled into the daemon.
RUN test "$BUILD_VERSION" = dev || test "$(a13n-envd --version)" = "a13n-envd $BUILD_VERSION"

USER sandbox
WORKDIR /workspace
ENV A13N_ENVD_EXECUTION_ISOLATION=disabled \
    A13N_ENVD_RUNTIME_DIR=/home/sandbox/.local/state/a13n-envd
ENTRYPOINT ["tini", "--"]
CMD ["a13n-envd"]
