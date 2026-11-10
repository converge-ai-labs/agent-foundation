"""Render a bounded guest installation job; providers retain target and lifecycle ownership."""

import base64
import hashlib
import shlex


def dispatch(
    script: str,
    expected: str,
    operation_id: str,
    *,
    root: str,
    timeout_seconds: int = 900,
) -> str:
    """Rejoin frozen work; only a new operation may retry a failed attempt."""
    encoded = base64.b64encode(script.encode()).decode()
    runner = base64.b64encode(
        (
            "set -u\nexec 8>&-\n"
            f"timeout --kill-after=10 {timeout_seconds} sh install.sh; code=$?\n"
            "if [ $code = 0 ]; then printf ready; else printf failed; fi > status\n"
        ).encode()
    ).decode()
    current = hashlib.sha256(script.encode()).hexdigest()
    return f"""set -eu
umask 077
install -d -m 0700 {shlex.quote(root)}
cd {shlex.quote(root)}
exec 8>dispatch.lock
flock -n 8 || {{ printf running; exit 0; }}
exec 9>install.lock
flock -n 9 || {{ printf running; exit 0; }}
expected={shlex.quote(expected)}
if [ -f revision ]; then
 [ "$(cat revision)" = "$expected" ] || {{ printf conflict; exit 0; }}
else
 [ "$expected" = {shlex.quote(current)} ] || {{ printf incompatible; exit 0; }}
 printf %s {encoded} | base64 -d > install.sh
 printf %s {runner} | base64 -d > runner.sh
 printf %s "$expected" > revision
fi
status=$(cat status 2>/dev/null || true)
if [ "$status" = ready ]; then printf ready; exit 0; fi
if [ -f attempt ] && [ "$(cat attempt)" = {shlex.quote(operation_id)} ]; then
 printf failed > status; printf failed; exit 0
fi
printf %s {shlex.quote(operation_id)} > attempt
printf running > status
nohup sh runner.sh > output 2>&1 < /dev/null &
printf running
"""
