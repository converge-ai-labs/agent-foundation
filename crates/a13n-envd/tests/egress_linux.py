"""Opt-in real-kernel test: python3 egress_linux.py /path/to/a13n-envd.

Run in a disposable Linux sandbox with namespace permissions and HTTPS access.
--local-network-fixture additionally requires CAP_NET_ADMIN in the outer sandbox.
Only generated fake credentials are used; no provider credential is sent to envd.
"""

import argparse
import base64
import json
import os
import select
import shlex
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path


class Device:
    def __init__(self, binary, root):
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        (root / ".a13n").mkdir()
        (root / ".a13n" / ".env").write_text("BROKER_PRIVATE=must-not-be-readable")
        config = root / "envd.json"
        config.write_text(
            json.dumps(
                {
                    "device_id": "device-egress-test",
                    "default_working_directory": str(self.workspace),
                    "full_control": True,
                    "egress": {"enabled": True},
                }
            )
        )
        self.log = (root / "stderr").open("wb")
        self.process = subprocess.Popen(
            [binary, "--config", str(config)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(root),
                "LANG": "C.UTF-8",
                "HOST_ONLY_SECRET": "must-not-be-inherited",
                "HTTPS_PROXY": "http://invalid.invalid:1",
                "A13N_ENVD_RUNTIME_DIR": str(root / "runtime"),
            },
        )
        self.session = None
        self.sequence = 0
        self.descriptor = self.call(
            "initialize",
            {"supported_protocol_versions": ["0.1"], "client": {"name": "egress-integration", "version": "1"}},
        )["descriptor"]

    def call(self, method, params, error=None):
        self.sequence += 1
        payload = {"jsonrpc": "2.0", "id": self.sequence, "method": method, "params": params}
        if self.session:
            payload["eip_session"] = self.session
        body = json.dumps(payload).encode()
        self.process.stdin.write(
            f"Content-Length: {len(body)}\r\nContent-Type: application/json; charset=utf-8\r\n\r\n".encode() + body
        )
        self.process.stdin.flush()
        header = b""
        deadline = time.monotonic() + 60
        while not header.endswith(b"\r\n\r\n"):
            assert time.monotonic() < deadline, f"timed out: {method}"
            if not select.select([self.process.stdout], [], [], 1)[0]:
                continue
            byte = os.read(self.process.stdout.fileno(), 1)
            assert byte, f"daemon disconnected: {method}"
            header += byte
        length = int(header.split(b"Content-Length: ")[1].split(b"\r\n")[0])
        body = b""
        while len(body) < length:
            assert time.monotonic() < deadline, f"partial response: {method}"
            if select.select([self.process.stdout], [], [], 1)[0]:
                chunk = os.read(self.process.stdout.fileno(), length - len(body))
                assert chunk, "EOF in response"
                body += chunk
        response = json.loads(body)
        if error:
            assert response["error"]["data"]["error_type"] == error, response
            return response["error"]
        assert "error" not in response, (response, Path(self.log.name).read_text()[-2000:])
        return response["result"]

    def open(self, policy):
        result = self.call(
            "session.open",
            {
                "expected_device_id": self.descriptor["device_id"],
                "expected_generation": self.descriptor["generation"],
                "protocol_version": "0.1",
                "egress": policy,
            },
        )["descriptor"]
        self.session = result["session_id"]
        self.profile = result["shell_profiles"][0]["profile_id"]
        assert self.call("environment.readiness", {"context": {"operation_id": "op-ready"}})["ready"]
        return result["egress"]

    def shell(self, script, operation=None, environment=None, error=None):
        request = {"command": {"kind": "shell", "profile_id": self.profile, "script": script}}
        if environment:
            request["environment"] = environment
        return self.call(
            "shell.exec",
            {"context": {"operation_id": operation or "op-" + uuid.uuid4().hex[:12]}, "request": request},
            error,
        )

    def success(self, script, **kwargs):
        result = self.shell(script, **kwargs)
        assert result["status"]["exit_code"] == 0, result
        return base64.b64decode(result["output"]["stdout"]["preview"]["data"] + "===").decode()

    def close(self):
        if self.session:
            assert self.call("session.close", {})["closed"]
            self.session = None

    def shutdown(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.log.close()


def network_fixture(device):
    address = "93.184.216.34"
    subprocess.run(["ip", "address", "add", address + "/32", "dev", "lo"], check=True)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tcp = socket.socket()
    try:
        udp.bind((address, 19432))
        tcp.bind((address, 19433))
        tcp.listen()
        udp.settimeout(15)
        tcp.settimeout(15)

        def echo_udp():
            payload, peer = udp.recvfrom(4096)
            udp.sendto(payload, peer)

        def echo_tcp():
            connection, _ = tcp.accept()
            with connection:
                connection.sendall(connection.recv(4096))

        workers = [threading.Thread(target=echo_udp), threading.Thread(target=echo_tcp)]
        for worker in workers:
            worker.start()
        script = "import socket\nfor kind,port in [(socket.SOCK_DGRAM,19432),(socket.SOCK_STREAM,19433)]:\n s=socket.socket(socket.AF_INET,kind);s.settimeout(5);s.connect(('93.184.216.34',port));s.sendall(b'egress-echo');assert s.recv(64)==b'egress-echo';s.close()"
        device.success("python3 -c " + shlex.quote(script))
        for worker in workers:
            worker.join(timeout=15)
            assert not worker.is_alive()
    finally:
        udp.close()
        tcp.close()
        subprocess.run(["ip", "address", "del", address + "/32", "dev", "lo"], check=True)


def broker_death(device):
    device.open({"allow_hosts": []})
    heartbeat = device.workspace / "heartbeat"
    script = (
        "import os,time; os.setsid(); "
        + f"path={str(heartbeat)!r}; "
        + "\nwhile True:\n open(path,'w').write(str(time.time_ns()));time.sleep(.05)"
    )
    device.success(
        "python3 -c "
        + shlex.quote(script)
        + " </dev/null >/dev/null 2>&1 & "
        + "for i in $(seq 1 100); do test -s "
        + shlex.quote(str(heartbeat))
        + " && exit 0; sleep .05; done; exit 1"
    )
    deadline = time.monotonic() + 5
    while not heartbeat.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert heartbeat.exists(), "detached descendant did not start"
    device.process.kill()
    device.process.wait(timeout=5)
    # PDEATHSIG and namespace PID 1 must kill even a descendant that called setsid.
    time.sleep(1)
    stopped = heartbeat.stat().st_mtime_ns
    time.sleep(0.3)
    assert heartbeat.stat().st_mtime_ns == stopped, "descendant survived broker death"
    device.session = None


def run(binary, local_network, http_host):
    with tempfile.TemporaryDirectory(prefix="a13n-egress-test-") as directory:
        root = Path(directory)
        device = Device(binary, root)
        try:
            user, password = "egress-user", uuid.uuid4().hex
            github = http_host == "api.github.com"
            value = "application/json" if github else base64.b64encode(f"{user}:{password}".encode()).decode()
            binding = {"env": "TEST_TOKEN", "value": value, "inject_hosts": [http_host]}
            status = device.open({"allow_hosts": [http_host, "93.184.216.34"], "secrets": [binding]})
            marker = status["secrets"][0]["sentinel"]
            assert status["revision"] == 1 and value not in json.dumps(status)
            baseline = 'printf \'%s\' "$TEST_TOKEN"; test -z "$HOST_ONLY_SECRET$HTTPS_PROXY"; test ! -w /etc'
            assert device.success(baseline, operation="op-replay") == marker
            if github:
                # GitHub rejects an unsupported Accept value with 415. A 200 after
                # injecting application/json proves replacement without a real token.
                device.success(
                    """test "$(curl --max-time 20 -sS -o /dev/null -w '%{http_code}' https://api.github.com/ -H 'Accept: application/xml')" = 415"""
                )
                auth = 'curl --fail-with-body --max-time 20 -sS https://api.github.com/ -H "Accept: $TEST_TOKEN"'
                assert "current_user_url" in json.loads(device.success(auth))
                echoed = device.success(auth.replace("-sS", "-sS -D -"))
            else:
                auth = f'curl --fail-with-body --max-time 20 -sS https://{http_host}/basic-auth/{user}/{password} -H "Authorization: Basic $TEST_TOKEN"'
                assert json.loads(device.success(auth))["authenticated"] is True
                echoed = device.success(
                    f'curl --fail-with-body --max-time 20 -sS https://{http_host}/headers -H "Authorization: Basic $TEST_TOKEN"'
                )
            assert marker in echoed and value not in echoed
            device.success(f"test ! -e {root}/.a13n/.env; test ! -s {root}/envd.json; touch {device.workspace}/allowed")
            device.call(
                "file.write_text",
                {
                    "context": {"operation_id": "op-ro"},
                    "path": {"path": "/etc/egress-test-denied"},
                    "mode": "create",
                    "text": "deny",
                },
                error="denied",
            )
            socket_check = "import socket\na,b=socket.socketpair();a.send(b'ok');assert b.recv(2)==b'ok'\ntry:\n socket.socket(socket.AF_UNIX)\nexcept PermissionError:\n pass\nelse:\n raise AssertionError('named Unix socket allowed')"
            device.success("python3 -c " + shlex.quote(socket_check))
            device.shell("true", environment={"unset": ["TEST_TOKEN"]}, error="invalid_params")
            device.call("egress.update", {"expected_revision": 99}, error="conflict")
            updated = device.call(
                "egress.update",
                {
                    "expected_revision": 1,
                    "set_secrets": [{"env": "NEW_TOKEN", "value": "second-fake-value", "inject_hosts": [http_host]}],
                },
            )["egress"]
            assert updated["revision"] == 2
            assert device.success(baseline, operation="op-replay") == marker
            assert device.success('printf "%s" "$NEW_TOKEN"').startswith("a13n_")
            if local_network:
                network_fixture(device)
            device.call(
                "egress.update",
                {"expected_revision": 2, "allow_hosts": [], "remove_secrets": ["TEST_TOKEN", "NEW_TOKEN"]},
            )
            device.success(f"curl --max-time 2 -sS https://{http_host}/headers >/dev/null 2>&1; test $? -ne 0")
            device.close()
            # Repeated successful closes must return reservations to Device capacity.
            for _ in range(3):
                device.open({"allow_hosts": []})
                device.close()
            broker_death(device)
            print(
                json.dumps(
                    {
                        "egress": "passed",
                        "https_auth": not github,
                        "header_substitution": True,
                        "redaction": True,
                        "hot_update": True,
                        "replay": True,
                        "filesystem": True,
                        "quota_reuse": True,
                        "broker_death": True,
                        "tcp_udp_fixture": local_network,
                    }
                )
            )
        finally:
            device.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("binary")
    parser.add_argument("--local-network-fixture", action="store_true")
    parser.add_argument(
        "--http-host", choices=["httpbin.org", "httpbingo.org", "api.github.com"], default="httpbin.org"
    )
    args = parser.parse_args()
    run(str(Path(args.binary).resolve()), args.local_network_fixture, args.http_host)
