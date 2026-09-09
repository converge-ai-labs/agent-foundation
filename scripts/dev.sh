#!/usr/bin/env bash
set -euo pipefail

# Give each service its own process group, including pnpm/uv descendants.
# Job control and the polling wait also work with macOS's Bash 3.2.
set -m
service_pids=()
service_names=()

cleanup() {
  trap '' INT TERM
  for pid in "${service_pids[@]}"; do
    kill -TERM -- "-$pid" 2>/dev/null || true
  done
  for pid in "${service_pids[@]}"; do
    wait "$pid" 2>/dev/null || true
  done
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

config_path="${1:-dev/service/local.toml}"
echo "Starting a13n Service and Console (http://127.0.0.1:5173). Press Ctrl+C to stop both."
uv run --locked python -m dev.service --config "$config_path" serve &
service_pids+=("$!")
service_names+=("a13n Service")

pnpm --dir frontend --filter a13n-console dev &
service_pids+=("$!")
service_names+=("Console")

# If either service exits, preserve its status and stop the other service.
while true; do
  for index in "${!service_pids[@]}"; do
    if ! kill -0 "${service_pids[$index]}" 2>/dev/null; then
      status=0
      wait "${service_pids[$index]}" || status=$?
      echo "${service_names[$index]} exited (status $status); stopping development services." >&2
      exit "$status"
    fi
  done
  sleep 1
done
