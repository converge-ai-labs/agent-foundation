"""Pinned, idempotent installation on each new Sprite; no reusable cloud image."""

import base64
import hashlib
import json

from a13n_harness.providers.environment.sprites.commands import command
from a13n_harness.providers.environment.sprites.shared import SpritesReference

SOURCE_REVISION = "25bab435d27e3d30fc038bfa8d2f20e5d70cf783"
RELEASE_URL = "https://github.com/converge-ai-labs/agent-foundation/releases/download/release/a13n-envd-v0.5.1/a13n-envd-0.5.1-x86_64-unknown-linux-gnu.tar.gz"
RELEASE_SHA256 = "f2fd6eace212c2e59d2b4d6a2dc69ec010235005ad332ac1ddd1932f7bca258c"
FILES = (
    (
        "deploy/docker/images/sandbox/entrypoint.sh",
        "/usr/local/bin/a13n-sandbox-entrypoint",
        "b3c53fb45406e3206ad6465bf4461b5fba385da67d1f11a3c534a83e066a0496",
    ),
    (
        "deploy/docker/images/sandbox/desktop.py",
        "/usr/local/bin/a13n-sandbox-desktop",
        "455fbe3413af57fe36fea0f4a88a1d5bfbfb6bbc6f044121d46f0ce210e785b9",
    ),
    (
        "deploy/docker/images/sandbox/init-skills.sh",
        "/usr/local/bin/a13n-sandbox-init",
        "e973546b911758e35c629d093d7b0496ce7e5af0342a7a10570f192a43508f47",
    ),
    (
        "packages/a13n-service/a13n_service/providers/environment/desktop_guest.py",
        "/opt/a13n-desktop-access.py",
        "7d31432661c8199a95bed5214088a2d3589872f787ad55b28566ba9b659b9571",
    ),
)


def installation_script() -> str:
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
        f"curl -fL --retry 2 --max-time 120 '{RELEASE_URL}' -o /tmp/envd.tar.gz && echo '{RELEASE_SHA256}  /tmp/envd.tar.gz' | sha256sum -c - && tar -xOzf /tmp/envd.tar.gz a13n-envd > /usr/local/bin/a13n-envd && chmod 0755 /usr/local/bin/a13n-envd && rm /tmp/envd.tar.gz",
    ]
    for source, destination, digest in FILES:
        url = f"https://raw.githubusercontent.com/converge-ai-labs/agent-foundation/{SOURCE_REVISION}/{source}"
        commands.append(
            f"curl -fL --retry 2 --max-time 60 '{url}' -o '{destination}' && echo '{digest}  {destination}' | sha256sum -c - && chmod 0755 '{destination}'"
        )
    commands.append(
        "test ! -e /var/lib/a13n-envd/http-token && test ! -e /run/a13n-desktop-access/control.sock && /usr/local/bin/a13n-envd --version"
    )
    return "set -eu\n" + "\n".join(commands)


def revision() -> str:
    return hashlib.sha256(installation_script().encode()).hexdigest()


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


async def install(reference: SpritesReference, operation_id: str) -> str:
    payload = json.dumps([base64.b64encode(installation_script().encode()).decode(), revision(), operation_id])
    return (
        await command(reference, ["sudo", "-n", "python3", "-I", "-c", _LAUNCH, payload], 30, max_output=64)
    ).strip()
