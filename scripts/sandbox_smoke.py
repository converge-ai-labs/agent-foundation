"""Exercise the published sandbox contract on a native Docker architecture."""

from __future__ import annotations

import argparse
import subprocess
import uuid


def docker(*args: str) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=180, check=False)
    if result.returncode:
        raise RuntimeError(f"docker {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


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
    parser.add_argument("--variant", choices=("standard", "slim"), required=True)
    args = parser.parse_args()
    check_image(args.image, args.variant)
    print(f"Sandbox {args.variant} smoke checks passed: {args.image}")


if __name__ == "__main__":
    main()
