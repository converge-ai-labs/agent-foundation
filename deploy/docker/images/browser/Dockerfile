# syntax=docker/dockerfile:1
FROM python:3.13-slim-trixie

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       chromium xvfb openbox xauth x11vnc websockify \
       ca-certificates fonts-noto-cjk fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 browser \
    && useradd --uid 1000 --gid browser --create-home browser \
    && install -d -m 0700 -o browser -g browser /home/browser/data

# Match the Service's locked WebSocket client for bounded graceful Chromium shutdown.
RUN python3 -m pip install --no-cache-dir websockets==15.0.1

COPY packages/a13n-service/a13n_service/providers/browser/runtime.py /opt/a13n-browser/runtime.py
COPY packages/a13n-service/a13n_service/providers/environment/desktop_guest.py /opt/a13n-browser/desktop_guest.py
COPY LICENSE /usr/share/licenses/a13n-browser/LICENSE
ARG BUILD_VERSION=dev
ARG BUILD_REVISION=unknown
LABEL org.opencontainers.image.title="a13n-browser" \
      org.opencontainers.image.description="Private retained Chromium runtime for Agent Foundation Service" \
      org.opencontainers.image.source="https://github.com/converge-ai-labs/agent-foundation" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.revision="${BUILD_REVISION}" \
      org.opencontainers.image.licenses="Apache-2.0"

USER browser
WORKDIR /home/browser
EXPOSE 9222 6081
# Container isolation replaces Chromium's unavailable user-namespace sandbox under the default Engine profile.
# No host mounts, privileged mode or runtime package installation are used.
ENTRYPOINT ["python3", "-I", "/opt/a13n-browser/runtime.py", "--root", "/home/browser/data", "--guest", "/opt/a13n-browser/desktop_guest.py", "--listen", "0.0.0.0", "--cdp-port", "9222", "--viewer-port", "6081", "--no-sandbox"]
