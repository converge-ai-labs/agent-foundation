#!/usr/bin/env bash
set -euo pipefail

backend_pid=""
frontend_pid=""

cleanup() {
  local status=$?
  trap - EXIT INT TERM HUP

  for pid in "$backend_pid" "$frontend_pid"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill -TERM "$pid" 2>/dev/null || true
    fi
  done
  for pid in "$backend_pid" "$frontend_pid"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" 2>/dev/null || true
    fi
  done

  exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

uv_run=(uv run --locked)
if [[ -f ".env" ]]; then
  uv_run+=(--env-file ".env")
fi

"${uv_run[@]}" bash -c '
  backend_command=(foundation-service serve)
  if [[ -z "${LOGFIRE_TOKEN:-}" ]] &&
    [[ "${A13N_HARNESS_TRACE_LEVEL:-off}" != "off" || "${A13N_HARNESS_METRICS:-off}" != "off" ]]; then
    backend_command=(opentelemetry-instrument foundation-service serve)
  fi
  exec "${backend_command[@]}"
' &
backend_pid=$!

npm --prefix apps/foundation-web run dev &
frontend_pid=$!

printf 'Foundation Service: http://127.0.0.1:8000\n'
printf 'Foundation Web:     http://127.0.0.1:5173\n'

while true; do
  for pid in "$backend_pid" "$frontend_pid"; do
    if ! kill -0 "$pid" 2>/dev/null; then
      set +e
      wait "$pid"
      status=$?
      set -e
      exit "$status"
    fi
  done
  sleep 0.2
done
