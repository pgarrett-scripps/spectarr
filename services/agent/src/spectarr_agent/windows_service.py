"""Minimal Windows Service Control Manager host for the acquisition agent."""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from collections.abc import Callable
from ctypes import wintypes


LOGGER = logging.getLogger(__name__)
SERVICE_NAME = "SpectarrAgent"
SERVICE_WIN32_OWN_PROCESS = 0x00000010
SERVICE_START_PENDING = 0x00000002
SERVICE_STOP_PENDING = 0x00000003
SERVICE_RUNNING = 0x00000004
SERVICE_STOPPED = 0x00000001
SERVICE_ACCEPT_STOP = 0x00000001
SERVICE_ACCEPT_SHUTDOWN = 0x00000004
SERVICE_CONTROL_STOP = 0x00000001
SERVICE_CONTROL_SHUTDOWN = 0x00000005
NO_ERROR = 0


class ServiceStatus(ctypes.Structure):
    _fields_ = [
        ("service_type", wintypes.DWORD),
        ("current_state", wintypes.DWORD),
        ("controls_accepted", wintypes.DWORD),
        ("win32_exit_code", wintypes.DWORD),
        ("service_specific_exit_code", wintypes.DWORD),
        ("check_point", wintypes.DWORD),
        ("wait_hint", wintypes.DWORD),
    ]


ServiceMainFunction = ctypes.WINFUNCTYPE(None, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR))
HandlerFunction = ctypes.WINFUNCTYPE(
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.LPVOID,
    wintypes.LPVOID,
)


class ServiceTableEntry(ctypes.Structure):
    _fields_ = [("service_name", wintypes.LPWSTR), ("service_main", ctypes.c_void_p)]


_status_handle = None
_status = ServiceStatus()
_stop_event = threading.Event()
_run_callback: Callable[[threading.Event], None] | None = None
_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
_advapi32.RegisterServiceCtrlHandlerExW.argtypes = [
    wintypes.LPCWSTR,
    HandlerFunction,
    wintypes.LPVOID,
]
_advapi32.RegisterServiceCtrlHandlerExW.restype = wintypes.HANDLE
_advapi32.SetServiceStatus.argtypes = [wintypes.HANDLE, ctypes.POINTER(ServiceStatus)]
_advapi32.SetServiceStatus.restype = wintypes.BOOL
_advapi32.StartServiceCtrlDispatcherW.argtypes = [ctypes.POINTER(ServiceTableEntry)]
_advapi32.StartServiceCtrlDispatcherW.restype = wintypes.BOOL


def _set_status(state: int, exit_code: int = NO_ERROR, wait_hint: int = 0) -> None:
    if _status_handle is None:
        return
    _status.service_type = SERVICE_WIN32_OWN_PROCESS
    _status.current_state = state
    _status.controls_accepted = (
        SERVICE_ACCEPT_STOP | SERVICE_ACCEPT_SHUTDOWN if state == SERVICE_RUNNING else 0
    )
    _status.win32_exit_code = exit_code
    _status.service_specific_exit_code = 0
    _status.check_point = 0
    _status.wait_hint = wait_hint
    if not _advapi32.SetServiceStatus(_status_handle, ctypes.byref(_status)):
        raise ctypes.WinError(ctypes.get_last_error())


@HandlerFunction
def _control_handler(control: int, event_type: int, event_data: object, context: object) -> int:
    del event_type, event_data, context
    if control in {SERVICE_CONTROL_STOP, SERVICE_CONTROL_SHUTDOWN}:
        _set_status(SERVICE_STOP_PENDING, wait_hint=30_000)
        _stop_event.set()
    return NO_ERROR


@ServiceMainFunction
def _service_main(argument_count: int, arguments: object) -> None:
    del argument_count, arguments
    global _status_handle
    _status_handle = _advapi32.RegisterServiceCtrlHandlerExW(SERVICE_NAME, _control_handler, None)
    if not _status_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    _set_status(SERVICE_START_PENDING, wait_hint=30_000)
    exit_code = NO_ERROR
    try:
        _set_status(SERVICE_RUNNING)
        if _run_callback is None:
            raise RuntimeError("Windows service callback is not configured")
        _run_callback(_stop_event)
    except Exception:
        exit_code = 1
        LOGGER.exception("Windows service stopped after an unhandled error")
    finally:
        _set_status(SERVICE_STOPPED, exit_code=exit_code)


def run_as_windows_service(callback: Callable[[threading.Event], None]) -> None:
    """Connect the current process to the Windows Service Control Manager."""

    if sys.platform != "win32":
        raise RuntimeError("--windows-service is supported only on Windows")
    global _run_callback
    _run_callback = callback
    _stop_event.clear()
    table = (ServiceTableEntry * 2)()
    table[0].service_name = SERVICE_NAME
    table[0].service_main = ctypes.cast(_service_main, ctypes.c_void_p).value
    if not _advapi32.StartServiceCtrlDispatcherW(table):
        raise ctypes.WinError(ctypes.get_last_error())
