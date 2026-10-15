"""Real managed monitoring in a disposable Linux container with namespace permissions.

Run with --cgroupns=private --cpus=0.5 --memory=512m. The test reads the
container's actual cgroup controllers; it never changes host cgroups.
"""

import os
import platform
import pwd
import shlex
import sys
import tempfile
import time
from pathlib import Path

from egress_linux import Device


def sample_after(device, sequence=0):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        report = device.call(
            "device.metrics",
            {
                "expected_device_id": device.descriptor["device_id"],
                "expected_generation": device.descriptor["generation"],
            },
            device_scope=True,
        )
        sample = report.get("sample")
        if sample and sample["sequence"] > sequence:
            return sample
        time.sleep(0.05)
    raise AssertionError("sampler did not advance")


def verify(binary, root):
    cgroup = Path("/sys/fs/cgroup")
    quota, period = (cgroup / "cpu.max").read_text().split()
    memory_limit = int((cgroup / "memory.max").read_text())
    assert int(quota) / int(period) == 0.5
    assert memory_limit == 512 * 1024 * 1024
    # Use the runtime's existing unprivileged account, with no sandbox-image dependency.
    execution_user = pwd.getpwnam("nobody")
    device = Device(
        binary,
        root,
        execution={"uid": execution_user.pw_uid, "gid": execution_user.pw_gid},
        egress="deny",
        sandbox={"mode": "disabled"},
        monitoring={
            "sample_interval_ms": 1000,
            "filesystems": {
                "workspace": str(root / "workspace"),
                "runtime": str(root / "runtime"),
                "missing": str(root / "missing"),
            },
            "cgroup_v2_directory": str(cgroup),
        },
    )
    try:
        assert device.session is None
        info = device.descriptor["system_info"]
        assert info["os"] == "linux"
        assert info["os_version"] == platform.freedesktop_os_release()["PRETTY_NAME"]
        first = sample_after(device)
        for name in ["workspace", "runtime"]:
            native = os.statvfs(root / name)
            assert first["filesystems"][name]["metrics"]["total_bytes"] == native.f_blocks * native.f_frsize
        assert first["filesystems"]["missing"] == {}
        group = first["cgroup_v2"]
        assert group["cpu_max"] == {"kind": "limited", "quota_usec": int(quota), "period_usec": int(period)}
        assert group["memory_max"] == {"kind": "limited", "bytes": memory_limit}
        assert group["memory_current_bytes"] > 0
        assert first["system"]["memory_total_bytes"] > memory_limit
        device.open()
        # A bounded payload exercises actual kernel CPU accounting and throttling.
        code = "import time; data=bytearray(8*1024*1024); end=time.monotonic()+3\nwhile time.monotonic()<end: pass"
        device.success("python3 -c " + shlex.quote(code))
        later = sample_after(device, first["sequence"])
        deadline = time.monotonic() + 15
        while later["cgroup_v2"]["cpu_usage_usec"] <= group["cpu_usage_usec"] + 100000:
            assert time.monotonic() < deadline
            later = sample_after(device, later["sequence"])
        assert later["cgroup_v2"]["cpu_throttled_usec"] > group["cpu_throttled_usec"]
        device.close()
        assert sample_after(device, later["sequence"])["sequence"] > later["sequence"]
    except BaseException:
        print(Path(device.log.name).read_text()[-4000:], file=sys.stderr)
        raise
    finally:
        device.shutdown()
    assert device.process.returncode == 0


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="envd-monitoring-") as directory:
        verify(str(Path(sys.argv[1]).resolve()), Path(directory))
    print("managed monitoring and real cgroup limits/accounting passed")
