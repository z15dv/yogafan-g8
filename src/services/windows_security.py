"""Small Windows ACL helpers for the local Safety Service boundary."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from multiprocessing import util
from multiprocessing.connection import (
    BUFSIZE,
    answer_challenge,
    deliver_challenge,
)
from pathlib import Path
import sys
from typing import Callable


TOKEN_QUERY = 0x0008
TOKEN_USER_CLASS = 1
SDDL_REVISION_1 = 1
SE_FILE_OBJECT = 1
DACL_SECURITY_INFORMATION = 0x00000004
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000


class WindowsSecurityError(OSError):
    pass


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("sid", wintypes.LPVOID), ("attributes", wintypes.DWORD)]


class _TokenUser(ctypes.Structure):
    _fields_ = [("user", _SidAndAttributes)]


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.DWORD),
        ("security_descriptor", wintypes.LPVOID),
        ("inherit_handle", wintypes.BOOL),
    ]


def _configure_apis():
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.LPVOID]
    kernel32.LocalFree.restype = wintypes.LPVOID
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = (
        wintypes.BOOL
    )
    advapi32.GetSecurityDescriptorDacl.argtypes = [
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.BOOL),
        ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.BOOL),
    ]
    advapi32.GetSecurityDescriptorDacl.restype = wintypes.BOOL
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.LPVOID,
    ]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    return kernel32, advapi32


def _require_windows() -> None:
    if sys.platform != "win32":
        raise WindowsSecurityError("Windows security descriptors require Windows")


def current_user_sid() -> str:
    """Return the process token's user SID, identical across UAC token levels."""
    _require_windows()
    kernel32, advapi32 = _configure_apis()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        raise ctypes.WinError(kernel32.GetLastError())
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(
            token, TOKEN_USER_CLASS, None, 0, ctypes.byref(size)
        )
        if not size.value:
            raise ctypes.WinError(kernel32.GetLastError())
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            token,
            TOKEN_USER_CLASS,
            buffer,
            size.value,
            ctypes.byref(size),
        ):
            raise ctypes.WinError(kernel32.GetLastError())
        token_user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
        sid_text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(
            token_user.user.sid, ctypes.byref(sid_text)
        ):
            raise ctypes.WinError(kernel32.GetLastError())
        try:
            return str(sid_text.value)
        finally:
            kernel32.LocalFree(ctypes.cast(sid_text, wintypes.LPVOID))
    finally:
        kernel32.CloseHandle(token)


def restricted_sddl(user_sid: str, *, user_rights: str = "GA") -> str:
    """Allow the intended principals and support a medium-integrity GUI."""
    if not user_sid.startswith("S-1-"):
        raise ValueError("Invalid Windows user SID")
    return (
        "D:P"
        "(A;;GA;;;SY)"
        "(A;;GA;;;BA)"
        f"(A;;{user_rights};;;{user_sid})"
        "S:(ML;;NW;;;ME)"
    )


def _with_dacl(sddl: str, apply: Callable[[wintypes.LPVOID], int]) -> None:
    _require_windows()
    kernel32, advapi32 = _configure_apis()
    descriptor = wintypes.LPVOID()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, SDDL_REVISION_1, ctypes.byref(descriptor), None
    ):
        raise ctypes.WinError(kernel32.GetLastError())
    try:
        present = wintypes.BOOL()
        defaulted = wintypes.BOOL()
        dacl = wintypes.LPVOID()
        if not advapi32.GetSecurityDescriptorDacl(
            descriptor,
            ctypes.byref(present),
            ctypes.byref(dacl),
            ctypes.byref(defaulted),
        ) or not present.value:
            raise ctypes.WinError(kernel32.GetLastError())
        result = int(apply(dacl))
        if result:
            raise ctypes.WinError(result)
    finally:
        kernel32.LocalFree(descriptor)


def _create_named_pipe_with_sddl(address: str, first: bool, sddl: str) -> int:
    """Create one pipe instance with its final DACL, avoiding an ACL race."""
    _require_windows()
    import _winapi

    kernel32, advapi32 = _configure_apis()
    descriptor = wintypes.LPVOID()
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, SDDL_REVISION_1, ctypes.byref(descriptor), None
    ):
        raise ctypes.WinError(kernel32.GetLastError())
    attributes = _SecurityAttributes(
        ctypes.sizeof(_SecurityAttributes), descriptor, False
    )
    flags = _winapi.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED
    if first:
        flags |= _winapi.FILE_FLAG_FIRST_PIPE_INSTANCE
    try:
        return _winapi.CreateNamedPipe(
            address,
            flags,
            _winapi.PIPE_TYPE_MESSAGE
            | _winapi.PIPE_READMODE_MESSAGE
            | _winapi.PIPE_WAIT,
            _winapi.PIPE_UNLIMITED_INSTANCES,
            BUFSIZE,
            BUFSIZE,
            _winapi.NMPWAIT_WAIT_FOREVER,
            ctypes.addressof(attributes),
        )
    finally:
        kernel32.LocalFree(descriptor)


class RestrictedPipeListener:
    """CPython-compatible AF_PIPE listener with creation-time security."""

    def __init__(self, address: str, user_sid: str | None = None) -> None:
        self._address = address
        self._sddl = restricted_sddl(user_sid or current_user_sid())
        self._handle_queue = [self._new_handle(first=True)]
        self._last_accepted = None
        self.close = util.Finalize(
            self,
            RestrictedPipeListener._finalize,
            args=(self._handle_queue, self._address),
            exitpriority=0,
        )

    def _new_handle(self, first: bool = False) -> int:
        return _create_named_pipe_with_sddl(self._address, first, self._sddl)

    def accept(self):
        import _winapi
        from multiprocessing.connection import PipeConnection

        self._handle_queue.append(self._new_handle())
        handle = self._handle_queue.pop(0)
        try:
            overlapped = _winapi.ConnectNamedPipe(handle, overlapped=True)
        except OSError as exc:
            if exc.winerror != _winapi.ERROR_NO_DATA:
                raise
        else:
            try:
                _winapi.WaitForMultipleObjects(
                    [overlapped.event], False, _winapi.INFINITE
                )
            except Exception:
                overlapped.cancel()
                _winapi.CloseHandle(handle)
                raise
            finally:
                _transferred, error = overlapped.GetOverlappedResult(True)
                if error:
                    _winapi.CloseHandle(handle)
                    raise ctypes.WinError(error)
        return PipeConnection(handle)

    @staticmethod
    def _finalize(queue, _address: str) -> None:
        import _winapi

        for handle in queue:
            _winapi.CloseHandle(handle)


class RestrictedAuthenticatedListener:
    """Authenticated listener matching multiprocessing.connection.Listener."""

    def __init__(
        self,
        address: str,
        *,
        family: str = "AF_PIPE",
        authkey: bytes,
        user_sid: str | None = None,
    ) -> None:
        if family != "AF_PIPE":
            raise ValueError("Restricted listener supports AF_PIPE only")
        if not isinstance(authkey, bytes) or len(authkey) < 32:
            raise ValueError("A strong byte-string auth key is required")
        self._listener = RestrictedPipeListener(address, user_sid)
        self._authkey = authkey

    def accept(self):
        connection = self._listener.accept()
        try:
            deliver_challenge(connection, self._authkey)
            answer_challenge(connection, self._authkey)
            return connection
        except Exception:
            connection.close()
            raise

    def close(self) -> None:
        self._listener.close()

    @property
    def address(self) -> str:
        return self._listener._address


def secure_file_for_current_user(path: Path, user_sid: str | None = None) -> None:
    """Restrict a provisioned key to SYSTEM, admins and its intended user."""
    path = Path(path)
    sid = user_sid or current_user_sid()
    sddl = restricted_sddl(sid, user_rights="GR")
    _kernel32, advapi32 = _configure_apis()

    def apply(dacl) -> int:
        return advapi32.SetNamedSecurityInfoW(
            str(path),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            dacl,
            None,
        )

    _with_dacl(sddl, apply)
