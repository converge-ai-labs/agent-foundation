"""Exercise the published sandbox contract on a native Docker architecture."""

from __future__ import annotations

import argparse
import subprocess
import textwrap
import uuid


def docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=180, check=False)
    if result.returncode:
        raise RuntimeError(f"docker {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def check_desktop(image: str) -> None:
    name = f"a13n-desktop-smoke-{uuid.uuid4().hex}"
    probe = textwrap.dedent("""\
        import json, pathlib, subprocess, time, urllib.error, urllib.request
        endpoint = 'http://127.0.0.1:8888/eip/control'
        credential = pathlib.Path('/var/lib/a13n-envd/http-token')
        deadline = time.monotonic() + 30
        while True:
            try:
                request = urllib.request.Request(endpoint, data=b'{}', method='POST')
                urllib.request.urlopen(request, timeout=2)
                raise AssertionError('Unauthenticated access was accepted')
            except urllib.error.HTTPError as error:
                assert error.code == 401, error.code
                break
            except urllib.error.URLError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.1)
        assert credential.stat().st_mode & 0o777 == 0o600
        request = urllib.request.Request(endpoint,
            data=b'{"jsonrpc":"2.0","id":1,"method":"device.describe","params":{}}',
            headers={'Authorization': 'Bearer ' + credential.read_text(),
                     'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(request, timeout=2) as response:
            assert json.load(response)['id'] == 1
        result = subprocess.run(['runuser', '-u', 'sandbox', '--', 'env',
            'DISPLAY=:99', 'XAUTHORITY=/run/a13n-desktop/Xauthority',
            'xprop', '-root', '_NET_SUPPORTING_WM_CHECK'],
            check=True, capture_output=True, text=True, timeout=2)
        assert 'window id' in result.stdout
        assert pathlib.Path('/workspace/.agents/skills/agent-browser/SKILL.md').is_file()
        """)
    try:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--network",
            "none",
            "-e",
            "A13N_ENVD_TRANSPORT=http",
            "-e",
            "A13N_ENVD_HTTP_BIND=127.0.0.1:8888",
            "-e",
            "A13N_ENVD_HTTP_PLAINTEXT_SCOPE=loopback",
            image,
        )
        docker("exec", name, "python3", "-c", probe)
        docker("exec", "--user", "sandbox", name, "touch", "/workspace/preserved")
        docker("stop", name)
        assert docker("inspect", "--format", "{{.State.ExitCode}}", name) == "0"
        docker("start", name)
        docker("exec", name, "python3", "-c", probe)
        docker("exec", name, "test", "-f", "/workspace/preserved")
        # Losing the desktop must fail the container instead of leaving a healthy-looking daemon.
        docker("exec", name, "pkill", "-x", "openbox")
        assert docker("wait", name) == "1"
    finally:
        docker("rm", "-f", name)
    check_desktop_access(image)


def check_desktop_access(image: str) -> None:
    """Check opt-in endpoint supervision without dependencies on the build host."""
    name = f"a13n-access-smoke-{uuid.uuid4().hex}"
    probe = textwrap.dedent("""\
        import base64, json, pathlib, subprocess, time
        root = '/run/a13n-desktop-access'
        deadline = time.monotonic() + 30
        while not pathlib.Path(root, 'control.sock').exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('Desktop access endpoint did not start')
            time.sleep(.1)
        assert pathlib.Path(root).stat().st_mode & 0o777 == 0o700
        assert subprocess.run(['runuser', '-u', 'sandbox', '--', 'test', '-r', root + '/control.sock']).returncode != 0
        def call(request):
            request['protocol_version'] = 1
            encoded = base64.b64encode(json.dumps(request).encode()).decode()
            result = subprocess.run(['python3', '-I', '/opt/a13n-desktop-access.py', 'call',
                                     '--root', root, '--request', encoded], capture_output=True, timeout=25)
            assert result.returncode == 0, 'Desktop access administration failed'
            body = json.loads(result.stdout)
            assert 'error' not in body, 'Desktop access operation failed'
            return body['result']
        generation = call({'operation': 'describe'})['generation']
        grant = {'id': 'grant_smoke', 'generation': generation, 'epoch': 1}
        assert call(dict(grant, operation='grant', mode='control', ttl_seconds=30))['state'] == 'active'
        inspected = call(dict(grant, operation='inspect'))
        assert 'token' not in inspected and 'password' not in inspected
        assert call(dict(grant, operation='revoke'))['state'] == 'revoked'
        assert call(dict(grant, operation='grant', mode='control', ttl_seconds=30))['state'] == 'revoked'
        """)
    try:
        docker(
            "run",
            "-d",
            "--name",
            name,
            "--network",
            "none",
            "-e",
            "A13N_DESKTOP_ACCESS=true",
            "-e",
            "A13N_ENVD_TRANSPORT=http",
            "-e",
            "A13N_ENVD_HTTP_BIND=127.0.0.1:8888",
            "-e",
            "A13N_ENVD_HTTP_PLAINTEXT_SCOPE=loopback",
            image,
        )
        docker("exec", name, "python3", "-c", probe)
        docker("exec", name, "pkill", "-f", "^python3 -I /opt/a13n-desktop-access.py serve ")
        assert docker("wait", name) == "1"
    finally:
        docker("rm", "-f", name)


def check_image(image: str, variant: str) -> None:
    assert docker("image", "inspect", "--format", "{{.Config.User}}", image) == "root"
    assert (
        docker("image", "inspect", "--format", '{{index .Config.Labels "ai.a13n.sandbox.variant"}}', image) == variant
    )
    # Closed stdin starts and then cleanly shuts down the default stdio daemon.
    assert docker("run", "--rm", "--network", "none", image) == ""
    docker(
        "run",
        "--rm",
        "--user",
        "sandbox",
        "--entrypoint",
        "sh",
        image,
        "-ec",
        'test "$(id -u):$(id -g)" = 1000:1000; '
        'test "$A13N_ENVD_EXECUTION_UID:$A13N_ENVD_EXECUTION_GID" = 1000:1000; '
        "test -w /workspace; test -w /home/sandbox; test -w /tmp/a13n; "
        "test -s /etc/ssl/certs/ca-certificates.crt; a13n-envd --version",
    )
    if variant == "slim":
        docker(
            "run",
            "--rm",
            "--entrypoint",
            "sh",
            image,
            "-ec",
            "for tool in python3 node npm uv pnpm cc sudo agent-browser chromium a13n-sandbox-init; do "
            '! command -v "$tool"; done; test ! -e /workspace/.agents',
        )
        return

    if variant == "desktop":
        check_desktop(image)
        return

    skill = ".agents/skills/agent-browser/SKILL.md"
    # Startup uses cwd, including non-root invocations, before executing CMD.
    for options in ([], ["--user", "sandbox"], ["--workdir", "/tmp/a13n"]):
        docker(
            "run",
            "--rm",
            *options,
            image,
            "sh",
            "-ec",
            f'test -s {skill}; test "$(stat -c %u:%g {skill})" = 1000:1000; '
            f"cmp {skill} /usr/local/share/a13n/skills/agent-browser/SKILL.md",
        )
    # Entrypoint replacement is an intentional opt-out; callers may reuse init.
    docker(
        "run",
        "--rm",
        "--user",
        "sandbox",
        "--entrypoint",
        "sh",
        image,
        "-ec",
        f"test ! -e .agents; a13n-sandbox-init; test -s {skill}",
    )
    docker(
        "run",
        "--rm",
        "--mount",
        "type=tmpfs,destination=/workspace,tmpfs-mode=0555",
        image,
        "sh",
        "-ec",
        "test ! -e .agents; a13n-envd --version",
    )

    volume = f"a13n-sandbox-smoke-{uuid.uuid4().hex}"
    docker("volume", "create", volume)
    try:
        mount = f"type=volume,source={volume},target=/workspace"
        docker(
            "run",
            "--rm",
            "--mount",
            mount,
            image,
            "sh",
            "-ec",
            f"test -s {skill}; runuser -u sandbox -- sh -c 'printf user-owned > {skill}'",
        )
        docker(
            "run",
            "--rm",
            "--mount",
            mount,
            image,
            "sh",
            "-ec",
            f'test "$(cat {skill})" = user-owned; test "$(stat -c %u:%g {skill})" = 1000:1000',
        )
    finally:
        docker("volume", "rm", volume)

    # No download/network, extra Docker capabilities or browser launch flags.
    docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        "sandbox",
        image,
        "sh",
        "-ec",
        "agent-browser skills get core >/tmp/a13n/browser-skill.md; "
        "test -s /tmp/a13n/browser-skill.md; "
        "trap 'agent-browser close' EXIT; "
        "agent-browser open 'data:text/html,<title>Sandbox smoke</title><button>Ready</button>'; "
        "agent-browser snapshot > /tmp/a13n/snapshot.txt; "
        "grep Ready /tmp/a13n/snapshot.txt; "
        "agent-browser screenshot /tmp/a13n/browser.png; test -s /tmp/a13n/browser.png",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--variant", choices=("standard", "slim", "desktop"), required=True)
    args = parser.parse_args()
    check_image(args.image, args.variant)
    print(f"Sandbox {args.variant} smoke checks passed: {args.image}")


if __name__ == "__main__":
    main()
