from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Any, cast

_CREATE_SUSPENDED = 0x00000004
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_TERMINATE = 0x0001
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_SUSPEND_RESUME = 0x0800


class _IoCounters(ctypes.Structure):
    _fields_ = (
        ("read_operation_count", ctypes.c_ulonglong),
        ("write_operation_count", ctypes.c_ulonglong),
        ("other_operation_count", ctypes.c_ulonglong),
        ("read_transfer_count", ctypes.c_ulonglong),
        ("write_transfer_count", ctypes.c_ulonglong),
        ("other_transfer_count", ctypes.c_ulonglong),
    )


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = (
        ("per_process_user_time_limit", ctypes.c_longlong),
        ("per_job_user_time_limit", ctypes.c_longlong),
        ("limit_flags", wintypes.DWORD),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority_class", wintypes.DWORD),
        ("scheduling_class", wintypes.DWORD),
    )


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = (
        ("basic_limit_information", _BasicLimitInformation),
        ("io_info", _IoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    )


class _BasicAccountingInformation(ctypes.Structure):
    _fields_ = (
        ("total_user_time", ctypes.c_longlong),
        ("total_kernel_time", ctypes.c_longlong),
        ("this_period_total_user_time", ctypes.c_longlong),
        ("this_period_total_kernel_time", ctypes.c_longlong),
        ("total_page_fault_count", wintypes.DWORD),
        ("total_processes", wintypes.DWORD),
        ("active_processes", wintypes.DWORD),
        ("total_terminated_processes", wintypes.DWORD),
    )


_windows_ctypes = cast(Any, ctypes)
_kernel32 = _windows_ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = _windows_ctypes.WinDLL("ntdll")

_CreateJobObjectW = _kernel32.CreateJobObjectW
_CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
_CreateJobObjectW.restype = wintypes.HANDLE

_SetInformationJobObject = _kernel32.SetInformationJobObject
_SetInformationJobObject.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
)
_SetInformationJobObject.restype = wintypes.BOOL

_QueryInformationJobObject = _kernel32.QueryInformationJobObject
_QueryInformationJobObject.argtypes = (
    wintypes.HANDLE,
    ctypes.c_int,
    wintypes.LPVOID,
    wintypes.DWORD,
    wintypes.LPVOID,
)
_QueryInformationJobObject.restype = wintypes.BOOL

_OpenProcess = _kernel32.OpenProcess
_OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_OpenProcess.restype = wintypes.HANDLE

_AssignProcessToJobObject = _kernel32.AssignProcessToJobObject
_AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
_AssignProcessToJobObject.restype = wintypes.BOOL

_TerminateJobObject = _kernel32.TerminateJobObject
_TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
_TerminateJobObject.restype = wintypes.BOOL

_CloseHandle = _kernel32.CloseHandle
_CloseHandle.argtypes = (wintypes.HANDLE,)
_CloseHandle.restype = wintypes.BOOL

_NtResumeProcess = _ntdll.NtResumeProcess
_NtResumeProcess.argtypes = (wintypes.HANDLE,)
_NtResumeProcess.restype = ctypes.c_long


def _windows_error() -> OSError:
    return _windows_ctypes.WinError(_windows_ctypes.get_last_error())


def _close_native_handle(handle: int) -> None:
    if not _CloseHandle(handle):
        raise _windows_error()


class WindowsJob:
    """Own one Windows process tree and kill every member on close."""

    def __init__(self, handle: int) -> None:
        self._handle: int | None = handle

    @classmethod
    def create(cls) -> WindowsJob:
        handle = _CreateJobObjectW(None, None)
        if not handle:
            raise _windows_error()
        information = _ExtendedLimitInformation()
        information.basic_limit_information.limit_flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not _SetInformationJobObject(
            handle,
            _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
        ):
            error = _windows_error()
            _close_native_handle(handle)
            raise error
        return cls(handle)

    @property
    def creation_flags(self) -> int:
        return _CREATE_SUSPENDED

    def assign_and_resume(self, process_id: int) -> None:
        process = _OpenProcess(
            _PROCESS_TERMINATE | _PROCESS_SET_QUOTA | _PROCESS_SUSPEND_RESUME,
            False,
            process_id,
        )
        if not process:
            raise _windows_error()
        error: BaseException | None = None
        try:
            if not _AssignProcessToJobObject(self._require_handle(), process):
                raise _windows_error()
            status = _NtResumeProcess(process)
            if status < 0:
                raise OSError(f"NtResumeProcess failed with NTSTATUS 0x{status & 0xFFFFFFFF:08x}")
        except BaseException as caught:
            error = caught
            raise
        finally:
            try:
                _close_native_handle(process)
            except OSError as close_error:
                if error is None:
                    raise
                error.add_note(f"Windows process handle cleanup also failed: {close_error!r}")

    def terminate_and_wait(self, *, timeout_seconds: float, poll_seconds: float) -> None:
        handle = self._require_handle()
        if not _TerminateJobObject(handle, 1):
            raise _windows_error()
        deadline = time.monotonic() + timeout_seconds
        while self.active_process_count() > 0:
            if time.monotonic() >= deadline:
                raise TimeoutError("Windows Job Object process tree did not terminate before the deadline")
            time.sleep(poll_seconds)

    def active_process_count(self) -> int:
        information = _BasicAccountingInformation()
        if not _QueryInformationJobObject(
            self._require_handle(),
            _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
            ctypes.byref(information),
            ctypes.sizeof(information),
            None,
        ):
            raise _windows_error()
        return int(information.active_processes)

    def close(self) -> None:
        handle = self._require_handle()
        _close_native_handle(handle)
        self._handle = None

    def _require_handle(self) -> int:
        if self._handle is None:
            raise RuntimeError("Windows Job Object is closed")
        return self._handle
