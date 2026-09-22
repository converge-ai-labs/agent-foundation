"""Native identity/sudo integration; run ONLY in a disposable Linux sandbox.

Requires root, an existing UID/GID 1000 account with NOPASSWD sudo, Python, ACL
utilities, and apt. It installs a package and creates a service account.
"""

import argparse
import os
import shlex
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from egress_linux import Device


def context():
    return {"operation_id": "op-" + uuid.uuid4().hex[:12]}


def verify_native_execution(binary, *, controlled=False):
    assert os.geteuid() == 0, "run this test in a disposable root-launched sandbox"
    with tempfile.TemporaryDirectory(prefix="envd-identity-") as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        device = Device(binary, root, egress=controlled, execution={"uid": 1000, "gid": 1000})
        policy = {} if controlled else None
        os.chown(device.workspace, 1000, 1000)
        try:
            verify_discovery(device, root)
            status = device.open(policy)
            assert (status is not None) == controlled
            assert device.success("id -u; id -ru; id -g; id -rg").splitlines() == ["1000"] * 4
            assert device.success("sudo -n id -u").strip() == "0"
            device.success(
                "python3 -c "
                + shlex.quote(
                    "from pathlib import Path; s=Path('/proc/self/status').read_text(); "
                    "assert 'CapEff:\\t0000000000000000' in s; "
                    "assert 'CapAmb:\\t0000000000000000' in s; "
                    "assert 'NoNewPrivs:\\t0' in s"
                )
            )
            description = device.call("environment.describe", {"context": context()})["descriptor"]
            assert (description.get("egress") is not None) == controlled
            assert ("egress.update" in description["available_methods"]) == controlled
            device.call(
                "file.write_text",
                {
                    "context": context(),
                    "path": {"path": str(device.workspace / "native.txt")},
                    "mode": "create",
                    "text": "native identity",
                },
            )
            metadata = (device.workspace / "native.txt").stat()
            assert (metadata.st_uid, metadata.st_gid) == (1000, 1000)
            device.call(
                "file.write_text",
                {
                    "context": context(),
                    "path": {"path": "/etc/a13n-unprivileged-write"},
                    "mode": "create",
                    "text": "must fail",
                },
                error="denied",
            )
            service = "a13n" + uuid.uuid4().hex[:8]
            device.success(
                f"sudo -n useradd --system {service}; "
                "sudo -n chown root:root native.txt; sudo -n chmod 600 native.txt; "
                f"sudo -n setfacl -m u:{service}:r native.txt; "
                f"sudo -n -u {service} cat native.txt"
            )
            assert (device.workspace / "native.txt").stat().st_uid == 0
            device.success("sudo -n apt-get install -y -qq hello; /usr/bin/hello")
            device.success("sudo -n update-ca-certificates; sudo -n ldconfig")
            device.success("sudo -n -i sh -c 'test $(id -u) = 0'; sudo -n -s sh -c 'test $(id -u) = 0'")
            verify_process_cleanup(device)
            if controlled:
                verify_build_install(device)
                verify_controlled_boundary(device)
            device.success("sudo -n sh -c 'echo persistent > /etc/a13n-identity-test'")
            device.success(
                "sudo -n python3 -c "
                + shlex.quote(
                    "import socket; from pathlib import Path; "
                    "s=socket.socket(socket.AF_UNIX); s.bind('service.sock'); "
                    "t=socket.socket(); t.bind(('127.0.0.1',80)); "
                    "s.close(); t.close(); Path('service.sock').unlink()"
                )
            )
            device.close()
            device.open(policy)
            assert device.success("cat /etc/a13n-identity-test").strip() == "persistent"
            device.success("/usr/bin/hello; sudo -n true")
            device.close()
            assert Path("/etc/a13n-identity-test").read_text().strip() == "persistent"
            assert not list(Path("/tmp").glob("a13n-session-*")), "worker runtime leaked"
        finally:
            device.shutdown()
    print(
        f"native identity, sudo, files, apt, service user, sockets, cleanup, persistence passed (controlled={controlled})"
    )


def verify_privilege_configuration(binary):
    cases = [
        ({}, {}, (), True),
        ({"allow_sudo": False}, {}, (), False),
        ({}, {"A13N_ENVD_ALLOW_SUDO": "false"}, (), False),
        ({}, {"A13N_ENVD_CONFIG_JSON": '{"execution":{"allow_sudo":false}}'}, (), False),
        ({}, {}, ("--allow-sudo", "false"), False),
        ({"allow_sudo": False}, {"A13N_ENVD_ALLOW_SUDO": "1"}, (), True),
        ({"allow_sudo": True}, {"A13N_ENVD_ALLOW_SUDO": "0"}, ("--allow-sudo", "true"), True),
        ({"allow_sudo": False}, {"A13N_ENVD_ALLOW_SUDO": "true"}, ("--allow-sudo", "0"), False),
    ]
    # The current-user path must enforce the same policy as a root launcher.
    for user in [None, 1000]:
        for execution, environment, arguments, enabled in cases:
            with tempfile.TemporaryDirectory(prefix="envd-privileges-") as temporary:
                root = Path(temporary)
                root.chmod(0o755)
                # Native setuid/file-capability fixtures, independent of sudoers.
                setuid = root / "setuid-id"
                shutil.copyfile("/usr/bin/id", setuid)
                setuid.chmod(0o4755)
                capable = root / "capable-cat"
                shutil.copyfile("/usr/bin/cat", capable)
                capable.chmod(0o755)
                device = Device(
                    binary,
                    root,
                    egress=False,
                    execution=execution,
                    environment=environment,
                    arguments=arguments,
                    user=user,
                )
                # Device's current-user fixture ownership is for daemon startup,
                # not these root-owned native escalation probes.
                os.chown(setuid, 0, 0)
                setuid.chmod(0o4755)
                os.chown(capable, 0, 0)
                subprocess.run(["setcap", "cap_dac_override=ep", str(capable)], check=True)
                os.chown(device.workspace, 1000, 1000)
                try:
                    device.open()
                    assert device.success("id -u").strip() == "1000"
                    assert device.success(f"{shlex.quote(str(setuid))} -u").strip() == ("0" if enabled else "1000")
                    status = device.success(f"{shlex.quote(str(capable))} /proc/self/status")
                    assert ("CapEff:\t0000000000000002" in status) == enabled, status
                    assert f"NoNewPrivs:\t{0 if enabled else 1}" in status, status
                    sudo = device.shell("sudo -n id -u")
                    assert (sudo["status"]["exit_code"] == 0) == enabled, sudo
                    if not enabled:
                        # Payload environment cannot override a trusted startup decision.
                        sudo = device.shell("A13N_ENVD_ALLOW_SUDO=true sudo -n id -u")
                        assert sudo["status"]["exit_code"] != 0, sudo
                    device.close()
                finally:
                    device.shutdown()
    print("default sudo, all startup layers, precedence, and inherited no-privilege-gain passed")


def verify_discovery(device, root):
    private = root / "root-only"
    private.mkdir(mode=0o700)
    params = {
        "expected_device_id": device.descriptor["device_id"],
        "expected_generation": device.descriptor["generation"],
        "path": str(private),
        "offset": 0,
        "limit": 100,
    }
    device.call("directory.list", params, error="denied")
    params["path"] = str(device.workspace)
    assert device.call("directory.list", params)["path"] == str(device.workspace)


def verify_process_cleanup(device):
    # Exercise process.start argv independently of shell.exec, including a sudo
    # descendant with a different UID. The supervisor must still reap its tree.
    result = device.call(
        "process.start",
        {
            "context": context(),
            "request": {
                "command": {
                    "kind": "argv",
                    "executable_spec": {"kind": "name", "name": "sudo"},
                    "arguments": ["-n", "sh", "-c", "echo root-ready > root-ready; sleep 120"],
                }
            },
        },
    )
    handle = result["process"]["handle"]
    deadline = time.monotonic() + 5
    while not (device.workspace / "root-ready").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert (device.workspace / "root-ready").exists()
    device.call("process.kill", {"context": context(), "handle": handle})
    result = device.call(
        "process.wait", {"context": {**context(), "timeout_ms": 10000}, "handle": handle, "condition": "tree_cleaned"}
    )
    assert result["process"]["status"]["cleanup"] == "complete", result


def verify_build_install(device):
    device.success("""set -eu
cat > native.c <<'EOF'
#include <stdio.h>
int main(void) { puts("native-install"); return 0; }
EOF
cat > CMakeLists.txt <<'EOF'
cmake_minimum_required(VERSION 3.16)
project(envd_native_install C)
add_executable(a13n-envd-native-install native.c)
install(TARGETS a13n-envd-native-install DESTINATION bin)
EOF
cmake -S . -B build -G 'Unix Makefiles' -DCMAKE_INSTALL_PREFIX=/usr/local
make -C build
sudo -n make -C build install
test "$(/usr/local/bin/a13n-envd-native-install)" = native-install
""")
    assert Path("/usr/local/bin/a13n-envd-native-install").is_file()


def verify_controlled_boundary(device):
    # sudo must not regain the capabilities or namespace operations that could
    # replace interception or enter the broker's mount/process namespaces.
    for command in [
        "unshare --user true",
        "unshare --net true",
        "mount -t tmpfs tmpfs /mnt",
        "ip link add escape0 type dummy",
        "nft flush ruleset",
    ]:
        assert device.shell("sudo -n " + command)["status"]["exit_code"] != 0, command
    device.success(
        "sudo -n python3 -c "
        + shlex.quote(
            "import socket,ctypes,errno; "
            "libc=ctypes.CDLL(None,use_errno=True); "
            "assert libc.socket(socket.AF_INET,socket.SOCK_RAW,1)==-1; "
            "assert ctypes.get_errno()==errno.EPERM; "
            "assert 'NoNewPrivs:\\t0' in open('/proc/self/status').read()"
        )
    )
    # PTYs are usable by native sudo/PAM, not globally disabled by the boundary.
    device.success("python3 -c " + shlex.quote("import pty,os; a,b=pty.openpty(); os.close(a); os.close(b)"))
    device.call("egress.update", {"expected_revision": 1, "allow_hosts": []})
    assert device.shell("sudo -n curl --max-time 2 -sS http://example.com/")["status"]["exit_code"] != 0


def verify_management_snapshot(binary):
    # Not under private /tmp: this exercises template masks, including ancestor
    # renames, rather than accidentally relying on the whole /tmp being hidden.
    root = Path(tempfile.mkdtemp(prefix="envd-snapshot-", dir="/var/tmp"))
    moved = root.with_name(root.name + "-moved")
    library = next(Path("/usr/lib").glob("*/libnftables.so.1")).resolve()
    helper = Path("/usr/sbin/ip").resolve()
    saved = [(path, path.read_bytes(), path.stat().st_mode) for path in [helper, library]]
    device = None
    try:
        device = Device(binary, root)
        device.open({"allow_hosts": []})
        for path, _, _ in saved:
            device.success(
                "sudo -n python3 -c "
                + shlex.quote(f"from pathlib import Path; Path({str(path)!r}).write_bytes(b'untrusted replacement')")
            )
        device.success("sudo -n mv " + shlex.quote(str(root)) + " " + shlex.quote(str(moved)))
        device.workspace = moved / "workspace"
        device.log.close()
        device.log = (moved / "stderr").open("ab")
        device.close()
        for policy in [{"allow_hosts": []}, None, {"allow_hosts": []}]:
            device.open(policy)
            device.success(
                "sudo -n sh -c "
                + shlex.quote(
                    f"test ! -s {moved}/envd.json; test ! -e {moved}/.a13n/.env; "
                    "test ! -e /bootstrap.json; test ! -e /credentials"
                )
            )
            assert device.success("id -u").strip() == "1000"
            device.close()
    finally:
        # Restore before starting another daemon: only the already-running
        # management snapshot is trusted while original helper files are changed.
        for path, data, mode in saved:
            path.write_bytes(data)
            path.chmod(mode)
        if device:
            device.shutdown()
        shutil.rmtree(moved if moved.exists() else root)
    print("management helper/dependency snapshot and renamed protected-path masks passed")


def verify_nss_bootstrap_handoff(binary):
    # A native NSS module can execute arbitrary code from the shared system tree.
    # It must not run before the broker receives the genuine worker-death handle.
    nsswitch = Path("/etc/nsswitch.conf")
    original = nsswitch.read_text()
    module = Path("/usr/lib/libnss_a13n_handoff.so.2")
    assert not module.exists(), "test NSS module already exists"
    with tempfile.TemporaryDirectory(prefix="envd-nss-handoff-", dir="/var/tmp") as temporary:
        root = Path(temporary)
        device = Device(binary, root)
        marker = device.workspace / "nss-handoff"
        source = root / "handoff.c"
        source.write_text(
            r"""
#include <fcntl.h>
#include <nss.h>
#include <poll.h>
#include <pwd.h>
#include <unistd.h>

/* The second bootstrap config arrives only after the broker receives pidfd.
 * Peek by polling, without consuming data needed by the normal worker. */
enum nss_status _nss_a13n_handoff_getpwuid_r(
    uid_t uid, struct passwd *pwd, char *buf, size_t size, int *err
) {
    int marker = open(MARKER, O_CREAT | O_EXCL | O_WRONLY, 0666);
    if (marker >= 0) {
        struct pollfd input = {.fd = 0, .events = POLLIN};
        char result = poll(&input, 1, 3000) > 0 && (input.revents & POLLIN) ? '1' : '0';
        if (write(marker, &result, 1) != 1) _exit(92);
        close(marker);
        if (result != '1') _exit(93);
    }
    return NSS_STATUS_UNAVAIL;
}
"""
        )
        try:
            subprocess.run(
                ["cc", "-shared", "-fPIC", f'-DMARKER="{marker}"', str(source), "-o", str(module)],
                check=True,
            )
            nsswitch.write_text(
                "\n".join(
                    "passwd: a13n_handoff files" if line.startswith("passwd:") else line
                    for line in original.splitlines()
                )
                + "\n"
            )
            for _ in range(3):
                device.open()
                assert marker.read_text() == "1", "native NSS ran before the kernel-handle handoff"
                # The NSS hook returns UNAVAIL so the native files backend still
                # resolves the account. Subsequent NSS calls leave the marker alone.
                assert device.success("id -u").strip() == "1000"
                device.close()
                marker.unlink()
        finally:
            nsswitch.write_text(original)
            module.unlink(missing_ok=True)
            device.shutdown()
    print("native NSS runs after the trusted bootstrap handoff and Session capacity is reusable")


def verify_managed_privilege_policy(binary):
    for execution, enabled in [({"allow_sudo": False}, False), ({"uid": 65534, "gid": 65534}, True)]:
        with tempfile.TemporaryDirectory(prefix="envd-managed-policy-") as temporary:
            root = Path(temporary)
            device = Device(binary, root, execution=execution)
            try:
                device.open({"allow_hosts": []})
                assert device.shell("sudo -n id -u")["status"]["exit_code"] != 0
                status = device.success("cat /proc/self/status")
                assert f"NoNewPrivs:\t{0 if enabled else 1}" in status, status
                # With NNP disabled, sudoers still denies the nobody account.
                if not enabled:
                    setuid = device.workspace / "setuid-id"
                    shutil.copyfile("/usr/bin/id", setuid)
                    setuid.chmod(0o4755)
                    assert device.success(str(setuid) + " -u").strip() == "1000"
                device.close()
            finally:
                device.shutdown()
    print("managed no-new-privileges and native denied sudoers passed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary")
    parser.add_argument(
        "--controlled", action="store_true", help="also test managed Sessions with native namespace permissions"
    )
    args = parser.parse_args()
    verify_native_execution(args.binary)
    verify_privilege_configuration(args.binary)
    if args.controlled:
        verify_native_execution(args.binary, controlled=True)
        verify_managed_privilege_policy(args.binary)
        verify_management_snapshot(args.binary)
        verify_nss_bootstrap_handoff(args.binary)
