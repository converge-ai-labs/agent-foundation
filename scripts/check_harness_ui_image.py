"""On-demand Docker smoke check; never called by routine CI or release jobs."""

from __future__ import annotations

import argparse
import json
import secrets
import subprocess
import time
import urllib.error
import urllib.request


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def check(image: str) -> None:
    name = f"harness-ui-check-{secrets.token_hex(5)}"
    volumes = {kind: f"{name}-{kind}" for kind in ("config", "data", "work")}
    targets = {"config": "/home/app/.a13n-harness-ui", "data": "/data", "work": "/work"}
    mounts = [
        argument
        for kind, volume in volumes.items()
        for argument in ("--mount", f"type=volume,src={volume},dst={targets[kind]}")
    ]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(url: str, key: str | None = None) -> tuple[int, bytes]:
        headers = {} if key is None else {"Authorization": f"Bearer {key}"}
        try:
            with opener.open(urllib.request.Request(url, headers=headers), timeout=2) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def start() -> tuple[str, str]:
        docker("run", "-d", "--name", name, "-p", "127.0.0.1::8765", *mounts, image)
        mapping = json.loads(docker("inspect", name))[0]["NetworkSettings"]["Ports"]["8765/tcp"][0]
        url = f"http://127.0.0.1:{mapping['HostPort']}"
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                if request(url + "/readyz")[0] == 200:
                    lines = docker("logs", name).splitlines()
                    key = next(line.removeprefix("API key: ") for line in lines if line.startswith("API key: "))
                    return url, key
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                pass
            time.sleep(0.2)
        raise AssertionError("Image did not become ready within 45 seconds")

    try:
        for volume in volumes.values():
            docker("volume", "create", volume)
        docker(
            "run",
            "--rm",
            *mounts,
            image,
            "sh",
            "-c",
            "printf 'schema_version: \"1\"\nprocess:\n  pricing_auto_update: false\n' > /home/app/.a13n-harness-ui/a13n-harness-ui.yaml",
        )
        url, key = start()
        assert docker("exec", name, "id", "-u") == "10001"
        assert request(url + "/healthz") == (200, b'{"status":"ok"}')
        assert request(url + "/api/status")[0] == 401
        assert request(url + "/")[0] == 200
        status_code, body = request(url + "/api/status", key)
        assert status_code == 200
        status = json.loads(body)
        installed = docker(
            "exec",
            name,
            "python",
            "-c",
            "import importlib.metadata; print(importlib.metadata.version('a13n-harness-ui'))",
        )
        assert status["version"] == installed
        assert not any(status["features"].values())
        assert docker("exec", name, "sh", "-c", "command -v git; command -v bash")
        for path in targets.values():
            docker("exec", name, "sh", "-c", f"printf persisted > {path}/smoke-marker")
        docker("stop", "--time", "15", name)
        assert docker("inspect", "--format", "{{.State.ExitCode}}", name) in {"0", "143"}
        docker("rm", name)
        # Replacement, not merely browser reconnect, exercises all persistent mounts.
        url, replacement_key = start()
        assert replacement_key != key
        assert request(url + "/api/status", key)[0] == 401
        assert request(url + "/api/status", replacement_key)[0] == 200
        for path in targets.values():
            assert docker("exec", name, "cat", f"{path}/smoke-marker") == "persisted"
        assert docker(
            "exec",
            name,
            "sh",
            "-c",
            "test -s /home/app/.a13n-harness-ui/a13n-harness-ui.yaml; find /data -name '*.sqlite*' -o -name '*.db'",
        )
        print(f"Passed: non-root, auth, assets, version {installed}, probes, key rotation, persistence, SIGTERM")
    finally:
        # Only resources created under this invocation's unpredictable name.
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        for volume in volumes.values():
            subprocess.run(
                ["docker", "volume", "rm", volume], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", nargs="?", default="a13n-harness-ui:local")
    check(parser.parse_args().image)
