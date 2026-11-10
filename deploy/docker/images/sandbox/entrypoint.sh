#!/bin/sh
set -eu

if command -v a13n-sandbox-init >/dev/null 2>&1 && ! a13n-sandbox-init; then
    printf '%s\n' 'a13n-sandbox-entrypoint: skill initialization failed; continuing without initialization' >&2
fi
# Credentials are private files, never command arguments. Atomic publication
# preserves the existing HTTP credential across restarts and read-only Secrets.
umask 077
if [ "${A13N_ENVD_TRANSPORT:-}" = "http" ]; then
    path=${A13N_ENVD_HTTP_CREDENTIAL_FILE:-/var/lib/a13n-envd/http-token}
    if [ ! -f "$path" ]; then
        mkdir -p "$(dirname "$path")"
        temporary=$(mktemp "$(dirname "$path")/.http-token.XXXXXX")
        trap 'rm -f "$temporary"' EXIT
        trap 'exit 1' HUP INT TERM
        od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "$temporary"
        ln "$temporary" "$path" 2>/dev/null || [ -f "$path" ]
        rm -f "$temporary"
        trap - EXIT HUP INT TERM
    fi
    export A13N_ENVD_HTTP_CREDENTIAL_FILE="$path"
fi
if [ "${A13N_ENVD_TRANSPORT:-}" = "reverse_websocket" ] && [ -n "${A13N_ENVD_SERVICE_BOOTSTRAP_TOKEN:-}" ]; then
    path=${A13N_ENVD_REVERSE_WS_CREDENTIAL_FILE:?}
    mkdir -p "$(dirname "$path")"
    temporary=$(mktemp "$(dirname "$path")/.service-token.XXXXXX")
    trap 'rm -f "$temporary"' EXIT
    trap 'exit 1' HUP INT TERM
    printf '%s' "$A13N_ENVD_SERVICE_BOOTSTRAP_TOKEN" > "$temporary"
    mv -f "$temporary" "$path"
    trap - EXIT HUP INT TERM
    unset A13N_ENVD_SERVICE_BOOTSTRAP_TOKEN
fi
exec "$@"
