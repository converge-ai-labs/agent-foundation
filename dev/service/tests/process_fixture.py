"""Real subprocess fixture for local lifecycle tests."""

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from dev.service.lifecycle import ProcessSpec, background_applications, lifecycle_lock, supervise


def sleep(path: Path | None = None) -> None:
    if path is not None:
        path.write_text(str(os.getpid()))
    while True:
        time.sleep(1)


def tree(path: Path) -> None:
    child = subprocess.Popen([sys.executable, __file__, "sleep"])
    path.write_text(str(child.pid))


def supervisor() -> None:
    command = (sys.executable, __file__, "sleep")
    signum = supervise(Path.cwd(), (ProcessSpec("first", command), ProcessSpec("second", command)))
    raise SystemExit(128 + signum if signum is not None else 0)


def background(root: Path, marker: Path) -> None:
    with background_applications(root):
        marker.write_text("ready")
        sleep()


if __name__ == "__main__":
    match sys.argv[1]:
        case "sleep":
            sleep(Path(sys.argv[2]) if len(sys.argv) == 3 else None)
        case "tree":
            tree(Path(sys.argv[2]))
        case "stubborn":
            marker = Path(sys.argv[2])
            signal.signal(signal.SIGTERM, lambda *_: marker.write_text("draining"))
            marker.write_text("ready")
            sleep()
        case "stubborn-supervisor":
            command = (sys.executable, __file__, "stubborn", sys.argv[2])
            signum = supervise(Path.cwd(), (ProcessSpec("stubborn", command),))
            raise SystemExit(128 + signum if signum is not None else 0)
        case "supervisor":
            supervisor()
        case "listener":
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", int(sys.argv[2])))
                listener.listen()
                Path(sys.argv[3]).write_text(str(os.getpid()))
                while True:
                    client, _ = listener.accept()
                    client.close()
        case "detached" | "detached-failure":
            from dev.service import __main__ as commands

            root = Path(sys.argv[2])
            ports = [int(port) for port in sys.argv[3:]]
            environment = SimpleNamespace(
                root=root, ports=SimpleNamespace(service=ports[0], model=ports[1], console=ports[2])
            )

            def run_dev(*args, **kwargs):
                if sys.argv[1] == "detached-failure":
                    raise RuntimeError("Injected application startup failure")
                supervise(
                    root,
                    tuple(
                        ProcessSpec(str(port), (sys.executable, __file__, "listener", str(port), str(root / str(port))))
                        for port in ports
                    ),
                )

            commands._run_dev = run_dev
            with lifecycle_lock(root):
                commands._run_detached(environment, root / "unused.toml", root / "unused.toml")
        case "background":
            background(Path(sys.argv[2]), Path(sys.argv[3]))
        case unexpected:
            raise ValueError(unexpected)
