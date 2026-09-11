"""On-demand Docker smoke check; never called by routine CI or release jobs."""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import subprocess
import time
import urllib.error
import urllib.request

from pycrdt import Doc, Map, Text
from websockets.sync.client import connect


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

    def request(
        url: str, key: str | None = None, *, data: dict[str, object] | None = None, method: str | None = None
    ) -> tuple[int, bytes]:
        headers = {} if key is None else {"Authorization": f"Bearer {key}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        body = None if data is None else json.dumps(data).encode()
        try:
            with opener.open(
                urllib.request.Request(url, headers=headers, data=body, method=method), timeout=2
            ) as response:
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
        assert status["features"]["host_files"] is True
        assert status["features"]["host_git"] is True
        assert status["features"]["host_terminal"] is True
        assert status["features"]["shared_drafts"] is True
        assert docker("exec", name, "sh", "-c", "command -v git; command -v bash")
        code, body = request(url + "/api/host/terminals", key, data={"cwd": "/work", "rows": 37, "columns": 111})
        assert code == 200
        terminal_id = json.loads(body)["terminal_id"]
        with connect(
            url.replace("http:", "ws:") + f"/api/host/terminals/{terminal_id}/connect", origin=url, proxy=None
        ) as terminal:
            terminal.send(json.dumps({"api_key": key}))
            opened = json.loads(terminal.recv(timeout=5))
            terminal.send(json.dumps({"kind": "control", "control_epoch": opened["terminal"]["control_epoch"]}))
            while True:
                controlled = json.loads(terminal.recv(timeout=5))
                if controlled["terminal"]["controller"] == opened["participant_id"]:
                    break
            terminal.send(
                json.dumps(
                    {
                        "kind": "input",
                        "control_epoch": controlled["terminal"]["control_epoch"],
                        "text": "stty -echo; test -t 0; stty size; id -u; git init -q -b main /work/git-fixture; printf 'container change\\n' > /work/git-fixture/file; printf '__%s__\\n' CREATED\n",
                    }
                )
            )
            output = bytearray()
            while b"__CREATED__" not in output:
                frame = json.loads(terminal.recv(timeout=5))
                output.extend(base64.b64decode(frame["data_base64"]))
            assert b"37 111" in output and b"10001" in output
        code, body = request(url + "/api/host/git/repository?path=/work/git-fixture", key)
        assert code == 200 and json.loads(body)["repository"]["head_oid"] is None
        code, body = request(url + "/api/host/git/status?path=/work/git-fixture", key)
        assert code == 200 and json.loads(body)["entries"][0]["kind"] == "untracked"
        with connect(
            url.replace("http:", "ws:") + f"/api/host/terminals/{terminal_id}/connect", origin=url, proxy=None
        ) as terminal:
            terminal.send(json.dumps({"api_key": key}))
            opened = json.loads(terminal.recv(timeout=5))
            terminal.send(json.dumps({"kind": "control", "control_epoch": opened["terminal"]["control_epoch"]}))
            while True:
                controlled = json.loads(terminal.recv(timeout=5))
                if controlled["terminal"]["controller"] == opened["participant_id"]:
                    break
            terminal.send(
                json.dumps(
                    {
                        "kind": "input",
                        "control_epoch": controlled["terminal"]["control_epoch"],
                        "text": "git -C /work/git-fixture add file; printf '__%s__\\n' STAGED\n",
                    }
                )
            )
            output = bytearray()
            while b"__STAGED__" not in output:
                frame = json.loads(terminal.recv(timeout=5))
                output.extend(base64.b64decode(frame["data_base64"]))
        code, body = request(
            url + "/api/host/git/diff?repository_path=/work/git-fixture&path=file&comparison=staged", key
        )
        assert code == 200 and "+container change" in json.loads(body)["text"]
        # A real installed CRDT binding, still without invoking a model/provider.
        for relative, resource in (
            (
                "models/image.yaml",
                {
                    "schema_version": "1",
                    "kind": "model",
                    "id": "model-image",
                    "name": "Image",
                    "route": "openai:gpt-5",
                    "authentication": {"kind": "api_key", "env": "UNUSED_IMAGE_TEST_KEY"},
                },
            ),
            (
                "agents/image.yaml",
                {"schema_version": "1", "kind": "agent", "id": "agent-image", "name": "Image", "model": "model-image"},
            ),
        ):
            code, body = request(
                url + "/api/configuration/sources/" + relative,
                key,
                method="PUT",
                data={"content": json.dumps(resource)},
            )
            assert code == 200, body
        code, body = request(url + "/api/threads", key, data={"defaults": {"agent_id": "agent-image"}})
        assert code == 200, body
        draft_thread_id = json.loads(body)["thread_id"]
        draft_path = f"/api/threads/{draft_thread_id}/draft/connect"
        with (
            connect(url.replace("http:", "ws:") + draft_path, origin=url, proxy=None) as first,
            connect(url.replace("http:", "ws:") + draft_path, origin=url, proxy=None) as second,
        ):
            first.send(json.dumps({"api_key": key}))
            second.send(json.dumps({"api_key": key}))
            opened = json.loads(first.recv(timeout=5))
            joined = json.loads(second.recv(timeout=5))
            draft_id = opened["draft_id"]
            assert joined["draft_id"] == draft_id
            doc = Doc({"text": Text(), "attachments": Map()})
            doc.apply_update(base64.b64decode(opened["update_base64"]))
            doc.get("text", type=Text).insert(0, "container coedit")
            first.send(
                json.dumps(
                    {"kind": "sync", "draft_id": draft_id, "update_base64": base64.b64encode(doc.get_update()).decode()}
                )
            )
            while True:
                frame = json.loads(second.recv(timeout=5))
                observed = Doc({"text": Text(), "attachments": Map()})
                observed.apply_update(base64.b64decode(frame["update_base64"]))
                if str(observed.get("text", type=Text)) == "container coedit":
                    break
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
        assert request(url + "/api/host/terminals", replacement_key) == (200, b"[]")
        assert request(url + f"/api/host/terminals/{terminal_id}", replacement_key)[0] == 404
        with connect(url.replace("http:", "ws:") + draft_path, origin=url, proxy=None) as restarted:
            restarted.send(json.dumps({"api_key": replacement_key}))
            frame = json.loads(restarted.recv(timeout=5))
            assert frame["draft_id"] != draft_id
            observed = Doc({"text": Text(), "attachments": Map()})
            observed.apply_update(base64.b64decode(frame["update_base64"]))
            assert str(observed.get("text", type=Text)) == ""
        for path in targets.values():
            assert docker("exec", name, "cat", f"{path}/smoke-marker") == "persisted"
        assert docker(
            "exec",
            name,
            "sh",
            "-c",
            "test -s /home/app/.a13n-harness-ui/a13n-harness-ui.yaml; find /data -name '*.sqlite*' -o -name '*.db'",
        )
        print(
            f"Passed: non-root PTY/resize/Git/CRDT, auth, assets, version {installed}, probes, key rotation, persistence, SIGTERM"
        )
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
