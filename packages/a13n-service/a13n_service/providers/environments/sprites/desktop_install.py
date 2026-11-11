"""Pinned, idempotent installation on each new Sprite; no reusable cloud image."""

import base64
import hashlib
import json

from a13n_harness.providers.environment.sprites.commands import command
from a13n_harness.providers.environment.sprites.shared import SpritesReference

from a13n_service.providers.environment import envd_assets
from a13n_service.providers.environment.envd_install import dispatch
from a13n_service.providers.environment.envd_release import installation_command


def installation_script(profile: envd_assets.Profile = "desktop") -> str:
    commands = [
        "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
        "python3 ca-certificates curl libgcc-s1 util-linux procps sudo xvfb openbox xauth x11-utils x11-xkb-utils "
        "x11vnc websockify fonts-noto-cjk fonts-noto-color-emoji xterm mousepad xdotool "
        "&& curl -fL --retry 2 --max-time 120 -o /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb "
        "&& apt-get install -y /tmp/google-chrome.deb && rm /tmp/google-chrome.deb "
        "&& ln -sf /usr/bin/google-chrome-stable /usr/local/bin/chromium "
        '&& (getent group sandbox >/dev/null || groupadd --gid 1002 sandbox) && (id sandbox >/dev/null 2>&1 || useradd --uid 1002 --gid sandbox --create-home --shell /bin/bash sandbox) && test "$(id -u sandbox)" = 1002 && test "$(id -g sandbox)" = 1002 '
        "&& install -d -o sandbox -g sandbox /workspace "
        "&& install -d -m 0700 /var/lib/a13n-envd /run/a13n-envd-state "
        "&& rm -rf /var/lib/apt/lists/*",
        installation_command(),
    ]
    if profile == "headless":
        commands[0] = (
            "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
            "ca-certificates curl libgcc-s1 util-linux procps sudo "
            "&& (getent group sandbox >/dev/null || groupadd --gid 1002 sandbox) "
            "&& (id sandbox >/dev/null 2>&1 || useradd --uid 1002 --gid sandbox --create-home --shell /bin/bash sandbox) "
            '&& test "$(id -u sandbox)" = 1002 && test "$(id -g sandbox)" = 1002 '
            "&& install -d -o sandbox -g sandbox /workspace "
            "&& install -d -m 0700 /var/lib/a13n-envd /run/a13n-envd-state "
            "&& rm -rf /var/lib/apt/lists/*"
        )
    commands.extend(envd_assets.commands(profile))
    commands.append(
        "test ! -e /var/lib/a13n-envd/http-token && test ! -e /run/a13n-desktop-access/control.sock && /usr/local/bin/a13n-envd --version"
    )
    return "set -eu\n" + "\n".join(commands)


def revision(profile: envd_assets.Profile = "desktop") -> str:
    return hashlib.sha256(installation_script(profile).encode()).hexdigest()


# The child inherits the locked fd. A lost response rejoins it; a dead child leaves
# a terminal failure. Only a new, explicitly requested operation may retry failure.
_LAUNCH = r"""
import base64,fcntl,json,os,pathlib,subprocess,sys
script,revision,attempt=json.loads(sys.argv[1])
root=pathlib.Path('/var/lib/a13n-sprites-install')
root.mkdir(mode=0o700,exist_ok=True)
lock=(root/'lock').open('w')
try:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:
    print('running');sys.exit(0)
status=root/'status'
old=status.read_text().strip() if status.exists() else ''
meta=root/'identity'
identity=json.loads(meta.read_text()) if meta.exists() else None
if identity and identity['revision']!=revision:
    print('conflict');sys.exit(0)
if old=='ready':
    print('ready');sys.exit(0)
if identity and identity['attempt']==attempt:
    status.write_text('failed');print('failed');sys.exit(0)
(root/'install.sh').write_bytes(base64.b64decode(script))
meta.write_text(json.dumps({'revision':revision,'attempt':attempt}))
status.write_text('running')
runner="timeout --kill-after=10 900 sh /var/lib/a13n-sprites-install/install.sh; code=$?; if [ $code = 0 ]; then printf ready; else printf failed; fi > /var/lib/a13n-sprites-install/status"
subprocess.Popen(['sh','-c',runner],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,pass_fds=(lock.fileno(),))
print('running')
"""


def headless_dispatch(operation_id: str, *, root: str = "/var/lib/a13n-sprites-headless-install") -> str:
    return dispatch(installation_script("headless"), revision("headless"), operation_id, root=root)


async def install(reference: SpritesReference, operation_id: str) -> str:
    if not reference.config.desktop:
        return (
            await command(reference, ["sudo", "-n", "sh", "-c", headless_dispatch(operation_id)], 30, max_output=64)
        ).strip()
    payload = json.dumps([base64.b64encode(installation_script().encode()).decode(), revision(), operation_id])
    return (
        await command(reference, ["sudo", "-n", "python3", "-I", "-c", _LAUNCH, payload], 30, max_output=64)
    ).strip()
