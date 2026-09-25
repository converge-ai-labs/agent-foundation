#!/bin/sh
# Started as root, as the single-host Compose stack does, the Service joins the group that owns the mounted Docker
# socket and drops to `app`, so no host-specific group ID is configured. Any other user runs the command as it is.
# Either way tini becomes process 1, reaping children and forwarding signals.
set -eu
if [ "$(id -u)" -ne 0 ]; then
    exec tini -- "$@"
fi
groups=app
if [ -S /var/run/docker.sock ]; then
    groups="$groups,$(stat -c %g /var/run/docker.sock)"
fi
exec setpriv --reuid=app --regid=app --groups="$groups" --inh-caps=-all --bounding-set=-all -- tini -- "$@"
