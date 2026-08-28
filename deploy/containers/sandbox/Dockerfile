# syntax=docker/dockerfile:1

FROM rust:1.96-bookworm AS builder

WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY crates/agent-envd crates/agent-envd
RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    cargo build --release --locked --package agent-envd

FROM debian:bookworm-slim AS runtime

ARG BUILD_VERSION=dev
ARG BUILD_REVISION=unknown
LABEL org.opencontainers.image.title="agent-foundation-sandbox" \
      org.opencontainers.image.description="Agent Foundation sandbox with agent-envd" \
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

COPY --from=builder /src/target/release/agent-envd /usr/local/bin/agent-envd
COPY LICENSE /usr/share/licenses/agent-envd/LICENSE

USER sandbox
WORKDIR /workspace
ENV AGENT_ENVD_EXECUTION_ISOLATION=disabled \
    AGENT_ENVD_RUNTIME_DIR=/home/sandbox/.local/state/agent-envd
ENTRYPOINT ["tini", "--"]
CMD ["agent-envd"]
