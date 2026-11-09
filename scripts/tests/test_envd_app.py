"""Exercise the actual Swift launcher controller with an owned disposable child."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Native macOS AppKit launcher")
ROOT = Path(__file__).resolve().parents[2]


def test_app_process_ownership_and_event_framing(tmp_path):
    native = ROOT / "crates/a13n-envd/macos"
    binary = tmp_path / "controller-test"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-swift-version",
            "5",
            "-parse-as-library",
            str(native / "Connection.swift"),
            str(native / "ConnectionTests.swift"),
            "-o",
            str(binary),
        ],
        check=True,
    )
    child = tmp_path / "envd-fixture"
    child.write_text(f"""#!{sys.executable}
import json, pathlib, signal, sys
if "--check-json" in sys.argv:
    print(json.dumps(dict(service="https://example.test/", workspace="ws_test", host="saved")))
    sys.exit(0)
if "--recognize" in sys.argv:
    if "invalid link" in sys.argv:
        print("Saved credential rejected", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(dict(registered=True)))
    sys.exit(0)
signal.signal(signal.SIGINT, signal.SIG_IGN)
with pathlib.Path(__file__).with_name("arguments.jsonl").open("a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
sys.stdout.write('{{"event":"verification","data":{{"code":"123456"}}}}\\n')
sys.stdout.flush()
sys.stdout.write('{{"event":"online","data":{{}}}}\\n')
sys.stdout.flush()
sys.stdin.buffer.read()
""")
    child.chmod(0o700)
    result = subprocess.run([str(binary), str(child)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    arguments = [json.loads(line) for line in (tmp_path / "arguments.jsonl").read_text().splitlines()]
    assert len(arguments) == 2
    assert all(
        args == ["connect-service", "saved", "--default-working-directory", "/tmp/a b;$(touch forbidden)", "--launcher"]
        for args in arguments
    )
    assert "PASS:" in result.stdout
