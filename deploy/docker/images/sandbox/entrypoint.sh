#!/bin/sh
set -eu

if ! a13n-sandbox-init; then
    printf '%s\n' 'a13n-sandbox-entrypoint: skill initialization failed; continuing without initialization' >&2
fi
exec "$@"
