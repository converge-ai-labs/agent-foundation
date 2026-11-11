#!/usr/bin/env bash
# Run a prebuilt native test executable in a disposable desktop and user bus.
set -uo pipefail
if [[ $# -lt 1 ]]; then
  echo 'Usage: bash dev/fixtures/linux-desktop/test-accessibility.sh <test-executable> [test-args...]' >&2
  exit 2
fi
sandbox=$(mktemp -d)
mkdir -p "$sandbox/home" "$sandbox/runtime"
chmod 700 "$sandbox/runtime"
trap 'rm -rf -- "$sandbox"' EXIT
env -u WAYLAND_DISPLAY -u DBUS_SESSION_BUS_ADDRESS -u AT_SPI_BUS_ADDRESS -u GTK_MODULES \
  HOME="$sandbox/home" XDG_RUNTIME_DIR="$sandbox/runtime" XDG_SESSION_TYPE=x11 \
  GIO_USE_VFS=local NO_AT_BRIDGE=0 GTK_A11Y=always A13N_ATSPI_TEST=1 \
  dbus-run-session -- xvfb-run -a -s '-screen 0 1280x1024x24 -nolisten tcp' "$@"
