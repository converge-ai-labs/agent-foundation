#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"
exec uv run --locked --package a13n-service python scripts/provider-smoke/openconnector.py "$@"
