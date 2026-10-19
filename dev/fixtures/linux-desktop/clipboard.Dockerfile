# Test-only additions to a current-source a13n-sandbox desktop image.
ARG SANDBOX_IMAGE=a13n-sandbox:local
FROM ${SANDBOX_IMAGE}
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-gi python3-xlib gir1.2-gtk-3.0 fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
