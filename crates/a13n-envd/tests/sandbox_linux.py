"""Real Session-worker Sandbox integration, using the host's rootless bubblewrap.

Run: python3 sandbox_linux.py /path/to/a13n-envd
No root, nftables, proxy, CA generation, or external network is required.
"""

import argparse
import json
import os
import shlex
import socket
import tempfile
from pathlib import Path

from egress_linux import Device, network_fixture


def verify_sandbox(binary, mode, uid=None):
    uid = os.geteuid() if uid is None else uid
    gid = os.getegid() if uid == os.geteuid() else uid
    policy = (
        {"destinations": {"mode": "allowlist", "hosts": ["example.com", "93.184.216.34"]}}
        if mode == "controlled"
        else None
    )
    with tempfile.TemporaryDirectory(prefix="a13n-sandbox-test-") as temporary:
        root = Path(temporary)
        readonly = root / "readonly"
        readonly.mkdir()
        (readonly / "input").write_text("read-only input")
        private = root / "private"
        private.write_text("must-not-be-visible")
        sandbox = {
            "mode": "restricted",
            "grants": [
                {"path": str(root / "workspace"), "access": "read_write"},
                {"path": str(readonly), "access": "read_only"},
            ],
        }
        device = Device(binary, root, egress=mode, sandbox=sandbox, execution={"uid": uid, "gid": gid})
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        try:
            device.open(policy)
            device.success('mkdir visible-directory; printf persistent > written; test -w "$HOME"; test -w "$TMPDIR"')
            assert (device.workspace / "written").read_text() == "persistent"
            device.call(
                "file.read_text",
                {
                    "context": {"operation_id": "op-private"},
                    "path": {"path": str(private)},
                    "line_limit": 10,
                    "max_line_length": 1024,
                },
                error="not_found_or_denied",
            )
            content = device.call(
                "file.read_text",
                {
                    "context": {"operation_id": "op-readonly"},
                    "path": {"path": str(readonly / "input")},
                    "line_limit": 10,
                    "max_line_length": 1024,
                },
            )
            assert content["text"] == "read-only input", content
            device.call(
                "file.write_text",
                {
                    "context": {"operation_id": "op-denied-write"},
                    "path": {"path": str(readonly / "output")},
                    "text": "not allowed",
                    "mode": "create",
                },
                error="denied",
            )
            probe = f"""import os,socket
assert os.geteuid()=={uid}
assert os.getegid()=={gid}
assert not os.path.exists({str(private)!r})
assert not os.path.exists({str(root / "envd.json")!r})
assert not os.path.exists('/proc/{os.getpid()}/status')
status=dict(line.split(':',1) for line in open('/proc/self/status') if ':' in line)
assert int(status['NoNewPrivs'])==1
for name in ['CapEff','CapPrm','CapInh','CapAmb','CapBnd']:
 assert int(status[name],16)==0,(name,status[name])
assert 'HOST_ONLY_SECRET' not in os.environ
assert 'HTTPS_PROXY' not in os.environ
s=socket.socket();s.settimeout(1)
try:
 s.connect(('127.0.0.1',{port}));connected=True
except OSError:
 connected=False
assert connected=={mode == "inherit"}
print('sandbox-ready')
"""
            assert device.success("python3 -c " + shlex.quote(probe)).strip() == "sandbox-ready"
            if mode == "controlled":
                network_fixture(device)
                device.success("curl --fail --silent --show-error --max-time 15 https://example.com >/dev/null")
                device.call(
                    "egress.update", {"expected_revision": 1, "destinations": {"mode": "allowlist", "hosts": []}}
                )
                device.success("if curl --fail --silent --max-time 3 https://example.com >/dev/null; then exit 1; fi")
            device.close()
            discovery = {
                "expected_device_id": device.descriptor["device_id"],
                "expected_generation": device.descriptor["generation"],
                "path": str(device.workspace),
                "offset": 0,
                "limit": 100,
            }
            listed = device.call("directory.list", discovery)
            assert "visible-directory" in json.dumps(listed), listed
            device.call("directory.list", {**discovery, "path": str(root / ".a13n")}, error="not_found_or_denied")
            device.open(policy)
            assert device.success("cat written") == "persistent"
            device.close()
        finally:
            listener.close()
            device.shutdown()
        print(f"PASS restricted+{mode} uid={uid}: files, grants, identity, capabilities, network, discovery, reopen")


def verify_grant_replacement(binary, mode):
    """A new Session/discovery must not re-authorize a replaced grant root."""
    with tempfile.TemporaryDirectory(prefix="a13n-grant-replacement-") as temporary:
        root = Path(temporary)
        sandbox = {"mode": "restricted", "grants": [{"path": str(root / "workspace"), "access": "read_write"}]}
        device = Device(binary, root, egress=mode, sandbox=sandbox)
        policy = {"destinations": {"mode": "allowlist", "hosts": []}} if mode == "controlled" else None
        try:
            device.open(policy)
            device.success("printf original > marker")
            device.close()
            device.workspace.rename(root / "original")
            ungranted = root / "ungranted"
            ungranted.mkdir()
            (ungranted / "marker").write_text("must-not-be-readable")
            device.workspace.symlink_to(ungranted)
            device.call(
                "session.open",
                {
                    "expected_device_id": device.descriptor["device_id"],
                    "expected_generation": device.descriptor["generation"],
                    "protocol_version": "0.1",
                    "working_directory": str(device.workspace),
                    **({"egress": policy} if policy is not None else {}),
                },
                error="unsupported",
            )
            device.call(
                "directory.list",
                {
                    "expected_device_id": device.descriptor["device_id"],
                    "expected_generation": device.descriptor["generation"],
                    "path": str(device.workspace),
                    "offset": 0,
                    "limit": 100,
                },
                error="internal_error",
            )
        finally:
            device.shutdown()
    print(f"PASS restricted+{mode}: replacement grant rejected for Session and discovery")


def verify_runtime_alias(binary):
    import subprocess

    with tempfile.TemporaryDirectory(prefix="a13n-runtime-alias-") as temporary:
        root = Path(temporary)
        control = root / "control"
        control.mkdir()
        alias = root / "alias"
        alias.symlink_to(control)
        config = {
            "device_id": "device-alias-test",
            "sandbox": {
                "mode": "restricted",
                "grants": [
                    {"path": str(control), "access": "read_write"},
                ],
            },
        }
        result = subprocess.run(
            [binary],
            input=b"",
            capture_output=True,
            timeout=15,
            env={
                "PATH": os.environ["PATH"],
                "A13N_ENVD_CONFIG_JSON": json.dumps(config),
                "A13N_ENVD_RUNTIME_DIR": str(alias / "runtime"),
            },
        )
        assert result.returncode != 0, result
        assert b"Sandbox grant overlaps a protected path" in result.stderr, result.stderr
    print("PASS runtime ancestor alias rejected before grant publication")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("binary")
    parser.add_argument(
        "--privileged", action="store_true", help="Test root and UID 1000 including controlled broker integration"
    )
    args = parser.parse_args()
    binary = str(Path(args.binary).resolve())
    verify_runtime_alias(binary)
    for mode in ["inherit", "controlled"] if args.privileged else ["inherit"]:
        verify_grant_replacement(binary, mode)
    for uid in [0, 1000] if args.privileged else [os.geteuid()]:
        for mode in ["inherit", "deny", "controlled"] if args.privileged else ["inherit", "deny"]:
            verify_sandbox(str(Path(args.binary).resolve()), mode, uid)


if __name__ == "__main__":
    main()
