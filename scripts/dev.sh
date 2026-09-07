#!/usr/bin/env bash
set -euo pipefail

uv_run=(uv run --locked)
if [[ -f ".env" ]]; then
  uv_run+=(--env-file ".env")
fi

exec "${uv_run[@]}" bash -c '
  backend_command=(a13n-service serve)
  if [[ -z "${LOGFIRE_TOKEN:-}" ]] &&
    [[ "${A13N_HARNESS_TRACE_LEVEL:-off}" != "off" || "${A13N_HARNESS_METRICS:-off}" != "off" ]]; then
    backend_command=(opentelemetry-instrument a13n-service serve)
  fi
  exec "${backend_command[@]}"
'
