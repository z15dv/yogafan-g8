"""Transient WinRing0 implementation for YogaFan G8."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
from pathlib import Path
import shutil
import struct
import sys
import time

from .backend import EcBackend

OLS_TYPE = 40000
METHOD_BUFFERED = 0
FILE_READ_ACCESS = 1
FILE_WRITE_ACCESS = 2


def _ctl_code(device_type: int, function: int, method: int, access: int) -> int:
    return (device_type << 16) | (access << 14) | (function << 2) | method


IOCTL_READ_BYTE = _ctl_code(OLS_TYPE, 0x833, METHOD_BUFFERED, FILE_READ_ACCESS)
IOCTL_WRITE_BYTE = _ctl_code(OLS_TYPE, 0x836, METHOD_BUFFERED, FILE_WRITE_ACCESS)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x01
FILE_SHARE_WRITE = 0x02
OPEN_EXISTING = 3
SC_MANAGER_ALL_ACCESS = 0xF003F
SERVICE_ALL_ACCESS = 0xF01FF
SERVICE_KERNEL_DRIVER = 0x01
SERVICE_DEMAND_START = 0x03
SERVICE_ERROR_NORMAL = 0x01
SERVICE_CONTROL_STOP = 0x01


def _source_driver_path() -> Path:
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS) / "WinRing0x64.sys"
    return Path(__file__).resolve().parents[2] / "WinRing0x64.sys"


def _stable_driver_path() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    directory = local_app_data / "YogaFan G8"
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / "WinRing0x64.sys"
    source = _source_driver_path()
    if not source.exists():
        raise FileNotFoundError("WinRing0x64.sys is missing from the application package")
    if not destination.exists() or source.stat().st_size != destination.stat().st_size:
        shutil.copy2(source, destination)
    return destination


class WinRing0Backend(EcBackend):
    """Short-lived driver service and device session.

    WinRing0 is a known vulnerable driver and may be blocked by Windows.  This
    class does not weaken Defender or add exclusions.
    """

    SERVICE_NAME = "YogaFanG8_WinRing0_Transient"
    DEVICE_NAME = r"\\.\WinRing0_1_2_0"

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("WinRing0 is available only on Windows")
        self.kernel32 = ctypes.windll.kernel32
        self.advapi32 = ctypes.windll.advapi32
        self._sc_manager = None
        self._service = None
        self.handle = None
        self._configure_api()
        self.invalid_handle = wintypes.HANDLE(-1).value

    def _configure_api(self) -> None:
        self.kernel32.CreateFileW.restype = wintypes.HANDLE
        self.kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self.kernel32.DeviceIoControl.restype = wintypes.BOOL
        self.kernel32.DeviceIoControl.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.advapi32.OpenSCManagerW.restype = wintypes.HANDLE
        self.advapi32.OpenSCManagerW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
        ]
        self.advapi32.OpenServiceW.restype = wintypes.HANDLE
        self.advapi32.OpenServiceW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            wintypes.DWORD,
        ]
        self.advapi32.CreateServiceW.restype = wintypes.HANDLE
        self.advapi32.CreateServiceW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPVOID,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
        ]
        self.advapi32.StartServiceW.restype = wintypes.BOOL
        self.advapi32.StartServiceW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPVOID,
        ]
        self.advapi32.ControlService.restype = wintypes.BOOL
        self.advapi32.ControlService.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        self.advapi32.DeleteService.restype = wintypes.BOOL
        self.advapi32.DeleteService.argtypes = [wintypes.HANDLE]
        self.advapi32.CloseServiceHandle.restype = wintypes.BOOL
        self.advapi32.CloseServiceHandle.argtypes = [wintypes.HANDLE]

    def _error(self, action: str) -> RuntimeError:
        code = self.kernel32.GetLastError()
        return RuntimeError(
            f"{action} failed (Windows error {code}). Run YogaFan G8 as "
            "Administrator. Windows Security may be blocking WinRing0; do not "
            "disable Defender or create an exclusion."
        )

    def open(self) -> None:
        if self.handle and self.handle != self.invalid_handle:
            return
        driver_path = str(_stable_driver_path())
        self._sc_manager = self.advapi32.OpenSCManagerW(
            None, None, SC_MANAGER_ALL_ACCESS
        )
        if not self._sc_manager:
            raise self._error("Opening the Service Control Manager")
        self._cleanup_existing_service()
        self._service = self.advapi32.CreateServiceW(
            self._sc_manager,
            self.SERVICE_NAME,
            "YogaFan G8 WinRing0 (Transient)",
            SERVICE_ALL_ACCESS,
            SERVICE_KERNEL_DRIVER,
            SERVICE_DEMAND_START,
            SERVICE_ERROR_NORMAL,
            driver_path,
            None,
            None,
            None,
            None,
            None,
        )
        if not self._service:
            error = self._error("Creating the transient WinRing0 service")
            self.close()
            raise error
        if not self.advapi32.StartServiceW(self._service, 0, None):
            error_code = self.kernel32.GetLastError()
            if error_code != 1056:
                error = self._error("Starting WinRing0")
                self.close()
                raise error
        self.handle = self.kernel32.CreateFileW(
            self.DEVICE_NAME,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if self.handle == self.invalid_handle:
            self.handle = None
            error = self._error("Opening the WinRing0 device")
            self.close()
            raise error

    def _cleanup_existing_service(self) -> None:
        service = self.advapi32.OpenServiceW(
            self._sc_manager, self.SERVICE_NAME, SERVICE_ALL_ACCESS
        )
        if service:
            status = ctypes.create_string_buffer(64)
            self.advapi32.ControlService(service, SERVICE_CONTROL_STOP, status)
            time.sleep(0.05)
            self.advapi32.DeleteService(service)
            self.advapi32.CloseServiceHandle(service)

    def close(self) -> None:
        if self.handle and self.handle != self.invalid_handle:
            self.kernel32.CloseHandle(self.handle)
        self.handle = None
        if self._service:
            status = ctypes.create_string_buffer(64)
            self.advapi32.ControlService(
                self._service, SERVICE_CONTROL_STOP, status
            )
            time.sleep(0.05)
            self.advapi32.DeleteService(self._service)
            self.advapi32.CloseServiceHandle(self._service)
            self._service = None
        if self._sc_manager:
            self.advapi32.CloseServiceHandle(self._sc_manager)
            self._sc_manager = None

    def read_io_port_byte(self, port: int) -> int:
        if not self.handle:
            raise RuntimeError("WinRing0 session is not open")
        input_buffer = struct.pack("<I", port)
        output_buffer = ctypes.create_string_buffer(4)
        returned = wintypes.DWORD()
        ok = self.kernel32.DeviceIoControl(
            self.handle,
            IOCTL_READ_BYTE,
            input_buffer,
            len(input_buffer),
            output_buffer,
            len(output_buffer),
            ctypes.byref(returned),
            None,
        )
        if not ok:
            raise self._error(f"Reading I/O port 0x{port:X}")
        return struct.unpack("<I", output_buffer.raw)[0] & 0xFF

    def write_io_port_byte(self, port: int, value: int) -> None:
        if not self.handle:
            raise RuntimeError("WinRing0 session is not open")
        input_buffer = struct.pack("<II", port, value & 0xFF)
        returned = wintypes.DWORD()
        ok = self.kernel32.DeviceIoControl(
            self.handle,
            IOCTL_WRITE_BYTE,
            input_buffer,
            len(input_buffer),
            None,
            0,
            ctypes.byref(returned),
            None,
        )
        if not ok:
            raise self._error(f"Writing I/O port 0x{port:X}")
