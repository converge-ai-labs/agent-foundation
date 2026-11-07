"""Opt-in public EIP VM integration: python3 vm_linux.py BINARY BACKEND.

Requires Linux KVM and the selected SDK feature. The 1 GiB guest budget covers
bootstrap of release binaries with both embedded runtimes. Strip debug binaries
before testing: BoxLite stages bootstrap artifacts in guest temporary storage.
"""

import argparse
import subprocess
import tempfile
import time
from pathlib import Path

from egress_linux import Device


def open_session(device, configuration):
    device.session = None
    descriptor = device.call(
        "session.open",
        {
            "expected_device_id": device.descriptor["device_id"],
            "expected_generation": device.descriptor["generation"],
            "configuration": configuration,
        },
        device_scope=True,
        timeout=180,
    )["descriptor"]
    device.session = descriptor["session_id"]
    device.profile = descriptor["shell_profiles"][0]["profile_id"]
    assert device.call("environment.readiness", {"context": {"operation_id": "op-ready"}})["ready"]
    return descriptor


def wait_until(predicate, description, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(description)


def verify(binary, backend):
    with tempfile.TemporaryDirectory(prefix="a13n-vm-public-") as temporary:
        root = Path(temporary)
        device = Device(
            binary,
            root,
            egress="inherit",
            execution={"allow_sudo": True},
            environment={"HTTPS_PROXY": "", "HTTP_PROXY": "", "ALL_PROXY": ""},
        )
        try:
            native = open_session(device, {})
            assert native["boundary"]["backend"] == "native", native
            assert device.success("printf native") == "native"
            configuration = {
                "working_directory": "/tmp",
                "isolation": {
                    "mode": "vm",
                    "backend": backend,
                    "image": "mirror.gcr.io/library/ubuntu:24.04",
                    "cpus": 1,
                    "memory_mib": 1024,
                    "mounts": [],
                },
            }
            shared = root / "shared"
            shared.mkdir()
            readonly = root / "readonly"
            readonly.mkdir()
            (readonly / "sentinel").write_text("original")
            configuration["isolation"]["mounts"] = [
                {"source": str(shared), "target": "/mnt/shared", "access": "read_write"},
                {"source": str(readonly), "target": "/mnt/readonly", "access": "read_only"},
            ]
            first = open_session(device, configuration)
            device.call(
                "file.write_text",
                {
                    "context": {"operation_id": "op-publish"},
                    "path": {"path": "/mnt/shared/published"},
                    "mode": "create",
                    "text": "published",
                },
            )
            assert (shared / "published").read_text() == "published"
            assert (
                device.success("set -eu; ! touch /mnt/readonly/forbidden 2>/dev/null; cat /mnt/readonly/sentinel")
                == "original"
            )
            device.call(
                "file.open_writer",
                {
                    "context": {"operation_id": "op-incomplete"},
                    "path": {"path": "/mnt/shared/incomplete"},
                    "mode": "create",
                },
            )
            assert first["boundary"]["backend"] == backend, first
            assert first["boundary"]["identity"] == {"uid": 0, "gid": 0}, first
            assert first["path_style"] == "posix", first
            assert first["working_directory"] == "/tmp", first
            assert {**first["boundary"]["vm"], "mounts": first["boundary"]["vm"].get("mounts", [])} == configuration[
                "isolation"
            ], first
            assert not any(method.startswith("computer.") for method in first["available_methods"])
            boot = device.success("cat /proc/sys/kernel/random/boot_id").strip()
            assert device.success('test "$(id -u)" = 0; test -z "$HOST_ONLY_SECRET"; printf guest') == "guest"
            open_session(device, configuration)
            assert device.success("cat /proc/sys/kernel/random/boot_id").strip() != boot
            device.close()
            device.session = first["session_id"]
            assert device.success("cat /proc/sys/kernel/random/boot_id").strip() == boot
            device.close()
            device.session = native["session_id"]
            assert device.success("printf still-native") == "still-native"
            device.close()
            # A replacement opens after physical cleanup, not after an ACK alone.
            open_session(device, configuration)
            device.close()
            assert not list((root / "runtime").glob("**/vms/session-*"))
            assert not list(shared.glob(".eip-stage-vm-*"))
            assert (shared / "published").read_text() == "published"
            assert not (shared / "incomplete").exists()

            # A guest worker crash loses execution, but releases owned storage
            # and capacity so another Session can open on the same Device.
            open_session(device, configuration)
            device.call(
                "file.open_writer",
                {
                    "context": {"operation_id": "op-crash-upload"},
                    "path": {"path": "/mnt/shared/crashed"},
                    "mode": "create",
                },
            )
            try:
                result = device.shell(
                    "for p in /proc/[0-9]*/cmdline; do "
                    'cmd=$(tr "\\000" " " < "$p" 2>/dev/null); '
                    'if [ "$cmd" = "/opt/a13n-worker/envd --internal-vm-worker " ]; then '
                    'pid=${p#/proc/}; pid=${pid%/cmdline}; kill -KILL "$pid"; fi; done; exit 77'
                )
                assert result["status"]["exit_code"] != 77, "guest worker was not found"
            except AssertionError as error:
                if str(error) == "guest worker was not found":
                    raise
                # The command may lose its RPC carrier before returning.
            device.session = None
            wait_until(lambda: not list((root / "runtime").glob("**/vms/session-*")), "guest crash reclamation")
            assert not list(shared.glob(".eip-stage-vm-*"))
            open_session(device, configuration)
            device.close()

            # SIGKILL leaves recovery records. Restart the same daemon directory
            # and require reclamation before the new generation is published.
            open_session(device, configuration)
            device.call(
                "file.open_writer",
                {
                    "context": {"operation_id": "op-restart-upload"},
                    "path": {"path": "/mnt/shared/restart"},
                    "mode": "create",
                },
            )
            old_generation = device.descriptor["generation"]
            launch = device.process.args
            device.process.kill()
            device.process.wait(timeout=15)
            device.process.stdin.close()
            device.process.stdout.close()
            device.session = None
            device.process = subprocess.Popen(
                launch,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=device.log,
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "HOME": str(root),
                    "A13N_ENVD_RUNTIME_DIR": str(root / "runtime"),
                },
            )
            device.descriptor = device.call("initialize", {"client": {"name": "vm-restart", "version": "1"}})[
                "descriptor"
            ]
            assert device.descriptor["generation"] != old_generation
            assert not list(shared.glob(".eip-stage-vm-*"))
            assert not list((root / "runtime").glob("**/vms/session-*"))
            open_session(device, configuration)
            device.close()
            assert (shared / "published").read_text() == "published"
            print(
                f"{backend}: coexistence, mounts, sibling close, guest crash and daemon restart reclamation passed",
                flush=True,
            )
        finally:
            device.shutdown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("binary")
    parser.add_argument("backend", choices=["boxlite", "microsandbox"])
    arguments = parser.parse_args()
    verify(str(Path(arguments.binary).resolve()), arguments.backend)
