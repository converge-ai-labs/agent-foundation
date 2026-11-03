"""Standalone Windows update worker, run by the base Python with -I -S.

Only standard-library imports: the uv tool environment can be replaced while
this process is running. The parent supplies this source via -c, not -m.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from ctypes import wintypes


def _kernel32():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.WaitForMultipleObjects.argtypes = (
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.BOOL,
        wintypes.DWORD,
    )
    kernel.WaitForMultipleObjects.restype = wintypes.DWORD
    return kernel


@contextmanager
def process_handles(pids: Sequence[int]) -> Iterator[list[int]]:
    """Capture process identity before handoff; inherit only these wait handles."""
    kernel = _kernel32()
    handles = []
    try:
        for pid in pids:
            handle = kernel.OpenProcess(0x00100000, True, pid)  # SYNCHRONIZE
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.append(handle)
        yield handles
    finally:
        for handle in handles:
            kernel.CloseHandle(handle)


def wait_for_exit(handles: Sequence[int], *, timeout: float = 60) -> None:
    """Wait for Python and its launchers, without holding any tool package open."""
    kernel = _kernel32()
    array = (wintypes.HANDLE * len(handles))(*handles)
    deadline = time.monotonic() + timeout
    try:
        while True:
            # Short native waits let Python deliver Ctrl+C while waiting.
            result = kernel.WaitForMultipleObjects(len(handles), array, True, 200)
            if result == 0:  # WAIT_OBJECT_0: all captured processes have exited.
                return
            if result != 258:  # WAIT_TIMEOUT
                raise ctypes.WinError(ctypes.get_last_error())
            if time.monotonic() >= deadline:
                raise TimeoutError("The launching processes did not exit; no update was started.")
    finally:
        for handle in handles:
            kernel.CloseHandle(handle)


def run(executable: str, tool_directory: str, handles: Sequence[int]) -> int:
    """Wait first, then invoke uv exactly once and report its actual outcome."""
    print("Waiting for a13n-harness-ui to exit...", flush=True)
    print("Close other Harness UI sessions before updating.", flush=True)
    print(f"Tool directory: {tool_directory}", flush=True)
    try:
        wait_for_exit(handles)
        print("Running: uv tool upgrade a13n-harness-ui", flush=True)
        result = subprocess.run(
            (executable, "tool", "upgrade", "a13n-harness-ui"),
            env={**os.environ, "UV_TOOL_DIR": tool_directory},
            check=False,
        )
    except KeyboardInterrupt:
        print("Update interrupted. Check the installation before retrying.", file=sys.stderr, flush=True)
        return 130
    except OSError as exc:
        print(f"Update could not complete: {exc}. No automatic retry was attempted.", file=sys.stderr, flush=True)
        return 1
    if result.returncode:
        print("Update failed. No automatic retry was attempted.", file=sys.stderr, flush=True)
        return result.returncode if result.returncode > 0 else 1
    print("Update complete. Restart a13n-harness-ui to use the new version.", flush=True)
    return 0


def main() -> int:
    code = run(sys.argv[1], sys.argv[2], [int(value) for value in sys.argv[3:]])
    # A dedicated console must retain the result instead of disappearing on exit.
    if sys.stdin.isatty():
        try:
            input("Press Enter to close this update window.")
        except (EOFError, KeyboardInterrupt):
            pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
