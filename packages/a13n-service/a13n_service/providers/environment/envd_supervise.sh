#!/bin/sh
# Guest-owned crash recovery. Readiness remains an authenticated EIP operation.
set -eu

# Stdio belongs to one caller and cannot transparently replace its protocol peer.
case ${A13N_ENVD_TRANSPORT:-stdio} in
    http|reverse_websocket) ;;
    *) exec "$@" ;;
esac

child=
pause=
stop() {
    trap '' HUP INT TERM
    if [ -n "$pause" ]; then kill "$pause" 2>/dev/null || true; fi
    if [ -n "$child" ]; then
        kill -TERM "-$child" 2>/dev/null || true
        # Finish inside desktop.py's five-second outer group deadline.
        # Leave two seconds for the watchdog, child reap and supervisor exit.
        (sleep 3; kill -KILL "-$child" 2>/dev/null || true) &
        watchdog=$!
        wait "$child" 2>/dev/null || true
        kill "$watchdog" 2>/dev/null || true
        wait "$watchdog" 2>/dev/null || true
        kill -KILL "-$child" 2>/dev/null || true
    fi
    exit 0
}
trap stop HUP INT TERM

# A finite budget avoids hiding permanent configuration errors in a crash loop.
# Native service/container policy owns any subsequent activation.
restarts=0
delay=1
while :; do
    setsid "$@" &
    child=$!
    code=0
    wait "$child" || code=$?
    # Retire the owned group; separately detached Session work is never replayed.
    kill -KILL "-$child" 2>/dev/null || true
    child=
    if [ "$restarts" -ge 3 ]; then
        printf 'a13n-envd-supervise: restart budget exhausted (exit %s)\n' "$code" >&2
        exit 1
    fi
    restarts=$((restarts + 1))
    printf 'a13n-envd-supervise: child exited (%s); restart %s/3 in %ss\n' "$code" "$restarts" "$delay" >&2
    sleep "$delay" &
    pause=$!
    wait "$pause" || true
    pause=
    delay=$((delay * 2))
done
