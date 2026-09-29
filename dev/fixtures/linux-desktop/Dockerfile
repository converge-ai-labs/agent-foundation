# Test-only desktop; never used by a shipped Environment provider.
FROM rust:1.97-bookworm AS native
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY crates ./crates
RUN CARGO_BUILD_JOBS=2 cargo build --locked -p a13n-envd

FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb openbox xauth x11-utils x11-xkb-utils x11-xserver-utils xdotool python3-tk fonts-dejavu-core tini \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY docs ./docs
COPY packages ./packages
COPY scripts ./scripts
COPY dev ./dev
COPY --from=native /src/target/debug/a13n-envd /usr/local/bin/a13n-envd
RUN UV_LINK_MODE=copy UV_CONCURRENT_BUILDS=1 UV_CONCURRENT_INSTALLS=2 UV_CONCURRENT_DOWNLOADS=4 \
    uv sync --locked --package a13n-harness-ui --no-default-groups --group test
RUN useradd --uid 10001 --create-home lab && chmod -R a+rX /app/packages /app/dev /app/scripts /app/docs
ENV DISPLAY=:99 HOME=/home/lab PYTHONDONTWRITEBYTECODE=1 \
    A13N_ENVD_TEST_BINARY=/usr/local/bin/a13n-envd A13N_X11_TEST=1
USER 10001:10001
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/.venv/bin/python", "-m", "pytest", "-p", "no:cacheprovider", "-v", "packages/a13n-harness-ui/tests/test_computer_x11.py"]
