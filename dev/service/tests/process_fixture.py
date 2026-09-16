"""Real subprocess fixture for local lifecycle tests."""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from dev.service.lifecycle import ProcessSpec, supervise


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
        case unexpected:
            raise ValueError(unexpected)
