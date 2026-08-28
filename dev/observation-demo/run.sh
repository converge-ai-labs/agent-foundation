#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
root_env="$repo_root/.env"

if [[ ! -f "$root_env" ]]; then
  printf 'Missing root environment file: %s\n' "$root_env" >&2
  exit 1
fi

if ! curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
  'http://127.0.0.1:3000/api/public/ready' >/dev/null; then
  printf 'Local Langfuse is not ready. Run `make langfuse-up` from %s first.\n' "$repo_root" >&2
  exit 1
fi

demo_env=(
  env
  -u CAPABILITY_TRACE_MODEL
  -u A13N_HARNESS_TRACE_LEVEL
  -u A13N_HARNESS_TRACE_CONTENT
  -u A13N_HARNESS_METRICS
  -u OTEL_SERVICE_NAME
  -u OTEL_SDK_DISABLED
  -u OTEL_TRACES_SAMPLER
  -u OTEL_TRACES_SAMPLER_ARG
  -u OTEL_TRACES_EXPORTER
  -u OTEL_METRICS_EXPORTER
  -u OTEL_EXPORTER_OTLP_PROTOCOL
  -u OTEL_EXPORTER_OTLP_TRACES_PROTOCOL
  -u OTEL_EXPORTER_OTLP_ENDPOINT
  -u OTEL_EXPORTER_OTLP_TRACES_ENDPOINT
  -u OTEL_EXPORTER_OTLP_HEADERS
  -u OTEL_EXPORTER_OTLP_TRACES_HEADERS
  -u OTEL_EXPORTER_OTLP_TIMEOUT
  -u OTEL_EXPORTER_OTLP_TRACES_TIMEOUT
  -u OTEL_EXPORTER_OTLP_COMPRESSION
  -u OTEL_EXPORTER_OTLP_TRACES_COMPRESSION
  -u OTEL_EXPORTER_OTLP_CERTIFICATE
  -u OTEL_EXPORTER_OTLP_TRACES_CERTIFICATE
  -u OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE
  -u OTEL_EXPORTER_OTLP_TRACES_CLIENT_CERTIFICATE
  -u OTEL_EXPORTER_OTLP_CLIENT_KEY
  -u OTEL_EXPORTER_OTLP_TRACES_CLIENT_KEY
  -u OTEL_EXPORTER_OTLP_INSECURE
  -u OTEL_EXPORTER_OTLP_TRACES_INSECURE
  -u OTEL_PYTHON_TRACER_PROVIDER
  -u LANGFUSE_PUBLIC_KEY
  -u LANGFUSE_SECRET_KEY
  -u LANGFUSE_BASE_URL
  -u LANGFUSE_HOST
  OTEL_SERVICE_NAME=agent-foundation-observation-demo
)

cd "$repo_root"
exec "${demo_env[@]}" uv run --locked --env-file "$root_env" \
  opentelemetry-instrument python "$script_dir/agent.py" "$@"
