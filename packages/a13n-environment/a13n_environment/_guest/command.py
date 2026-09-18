"""One-shot Linux command runner. Sent as source; no resident service is installed."""

import base64
import json
import os
import selectors
import signal
import subprocess
import sys
import time


def main():
    request = json.loads(sys.argv[1])
    environment = dict(os.environ)
    environment.update(request["env"])
    for key in request["unset"]:
        environment.pop(key, None)
    process = subprocess.Popen(
        request["argv"],
        cwd=request["cwd"],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert process.stdout is not None and process.stderr is not None
    output = [bytearray(), bytearray()]
    produced = [0, 0]
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, 0)
    selector.register(process.stderr, selectors.EVENT_READ, 1)
    deadline = time.monotonic() + request["timeout"]
    timed_out = False
    cleanup = "complete"
    try:
        while selector.get_map():
            if time.monotonic() >= deadline:
                timed_out = True
                break
            for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                index = key.data
                produced[index] += len(chunk)
                output[index].extend(chunk[: max(0, request["limit"] - len(output[index]))])
        if not timed_out:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
    finally:
        # Bound descendants as well, including children that keep inherited pipes open.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            cleanup = "failed"
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cleanup = "failed"
        selector.close()
        process.stdout.close()
        process.stderr.close()
    print(
        json.dumps(
            {
                "exit_code": process.returncode if process.returncode is not None else -1,
                "timed_out": timed_out,
                "cleanup": cleanup,
                "stdout": base64.b64encode(output[0]).decode(),
                "stderr": base64.b64encode(output[1]).decode(),
                "produced": produced,
            }
        )
    )


if __name__ == "__main__":
    main()
