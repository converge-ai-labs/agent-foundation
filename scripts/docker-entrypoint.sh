#!/bin/sh
set -eu

if [ "${1:-}" = "a13n-service" ] && [ "${2:-}" = "serve" ]; then
    role="${A13N_SERVICE_ROLE:-all}"
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
            if [ "${A13N_SERVICE_AUTO_MIGRATE:-false}" = "true" ]; then
                echo "Running advisory-locked database migrations before service startup."
                a13n-service db upgrade
            else
                echo "Auto migration disabled: checking schema heads without mutation."
                a13n-service db current --check-heads
            fi
            ;;
        worker | connectivity)
            echo "$role role: checking schema heads without running migrations."
            a13n-service db current --check-heads
            ;;
        *)
            echo "Invalid A13N_SERVICE_ROLE: $role" >&2
            exit 2
            ;;
    esac
fi

exec "$@"
