#!/bin/sh
set -eu

if [ "${1:-}" = "foundation-service" ] && [ "${2:-}" = "serve" ]; then
    role="${FOUNDATION_ROLE:-all}"
    expect_role_value=false
    for argument in "$@"; do
        if [ "$expect_role_value" = "true" ]; then
            role="$argument"
            expect_role_value=false
            continue
        fi
        case "$argument" in
            --role)
                expect_role_value=true
                ;;
            --role=*)
                role="${argument#--role=}"
                ;;
        esac
    done
    if [ "$expect_role_value" = "true" ]; then
        echo "Missing value for --role." >&2
        exit 2
    fi

    case "$role" in
        all | control)
            if [ "${FOUNDATION_AUTO_MIGRATE:-false}" = "true" ]; then
                echo "Running advisory-locked database migrations before service startup."
                foundation-service db upgrade
            else
                echo "Auto migration disabled: checking schema heads without mutation."
                foundation-service db current --check-heads
            fi
            ;;
        worker | connectivity)
            echo "$role role: checking schema heads without running migrations."
            foundation-service db current --check-heads
            ;;
        *)
            echo "Invalid FOUNDATION_ROLE: $role" >&2
            exit 2
            ;;
    esac
fi

exec "$@"
