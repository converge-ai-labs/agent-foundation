#!/bin/sh
# Disposable Service fixture only. Publish port 8888 on host loopback, never a public address.
set -eu
mkdir -p /tmp/work /tmp/runtime /tmp/installation
printf '%s' 'service-x11-fixture-token-not-a-secret' > /tmp/token
printf '%s' '{"device_id":"service-x11-fixture","computer_use":true,"default_working_directory":"/tmp/work"}' > /tmp/envd.json
Xvfb :99 -screen 0 1024x768x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
tries=0
until xdpyinfo >/dev/null 2>&1; do
    tries=$((tries + 1))
    if [ "$tries" -ge 150 ]; then exit 1; fi
    sleep .1
done
openbox >/tmp/openbox.log 2>&1 &
tries=0
until xprop -root _NET_SUPPORTING_WM_CHECK 2>/dev/null | grep -q 'window id'; do
    tries=$((tries + 1))
    if [ "$tries" -ge 150 ]; then exit 1; fi
    sleep .1
done
/usr/bin/python3 /app/dev/fixtures/linux-desktop/desktop.py /tmp/work/desktop.json >/tmp/desktop.log 2>&1 &
tries=0
until test -f /tmp/work/desktop.json; do
    tries=$((tries + 1))
    if [ "$tries" -ge 150 ]; then exit 1; fi
    sleep .1
done
export A13N_ENVD_RUNTIME_DIR=/tmp/runtime A13N_ENVD_STATE_DIR=/tmp/installation
export A13N_ENVD_TRANSPORT=http A13N_ENVD_HTTP_BIND=0.0.0.0:8888
export A13N_ENVD_HTTP_CREDENTIAL_FILE=/tmp/token A13N_ENVD_HTTP_PLAINTEXT_SCOPE=provider_private_link
exec a13n-envd --config /tmp/envd.json
