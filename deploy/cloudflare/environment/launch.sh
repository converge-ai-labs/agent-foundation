#!/bin/sh
set -eu
umask 077
if [ "$A13N_ENVD_TRANSPORT" = http ]; then
    printf '%s' "$A13N_CLOUDFLARE_HTTP_TOKEN" > "$A13N_ENVD_HTTP_CREDENTIAL_FILE"
    unset A13N_CLOUDFLARE_HTTP_TOKEN
fi
exec a13n-sandbox-entrypoint "$@"
