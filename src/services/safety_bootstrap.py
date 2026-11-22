"""Explicit Windows bootstrap for the development Safety Service.

The normal GUI remains unprivileged.  It may ask Windows to start a separate
elevated copy only after the user explicitly accepts the UAC-backed setup.
Nothing is registered for persistence and this module never opens the EC.
"""

from __future__ import annotations

import ctypes
from multiprocessing.connection import Client
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Callable

from .safety_service import DEFAULT_PIPE, default_authkey_path, load_authkey


class SafetyServiceLaunchError(RuntimeError):
    pass


def elevated_service_command(
    *,
    executable: str | None = None,
    main_script: Path | None = None,
    frozen: bool | None = None,
) -> tuple[str, list[str], str]:
    """Return the program, arguments and working directory for UAC launch."""
    program = str(executable or sys.executable)
    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    arguments = ["--safety-service", "--provision-if-missing"]
    if not is_frozen:
        script = Path(main_script or Path(__file__).resolve().parents[1] / "main.py")
        arguments.insert(0, str(script))
        working_directory = str(script.parent)
    else:
        working_directory = str(Path(program).resolve().parent)
    return program, arguments, working_directory


def launch_elevated_safety_service(
    *,
    shell_execute=None,
    executable: str | None = None,
    main_script: Path | None = None,
    frozen: bool | None = None,
) -> None:
    """Request an independent elevated server through the standard UAC UI."""
    if sys.platform != "win32":
        raise SafetyServiceLaunchError(
            "Safety Service setup is available only on Windows"
        )
    program, arguments, working_directory = elevated_service_command(
        executable=executable, main_script=main_script, frozen=frozen
    )
    execute = shell_execute or ctypes.windll.shell32.ShellExecuteW
    if shell_execute is None:
        execute.restype = ctypes.c_void_p
    result = execute(
        None,
        "runas",
        program,
        subprocess.list2cmdline(arguments),
        working_directory,
        0,  # SW_HIDE: the independent server has no interactive console.
    )
    result_code = int(result or 0)
    if result_code <= 32:
        raise SafetyServiceLaunchError(
            "Windows declined or could not start the elevated Safety Service "
            f"(ShellExecute code {result_code})."
        )


def safety_pipe_available(address: str = DEFAULT_PIPE) -> bool:
    """Check named-pipe readiness without opening a control lease."""
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.kernel32.WaitNamedPipeW(address, 0))
    except Exception:
        return False


def probe_safety_service(
    authkey: bytes,
    address: str = DEFAULT_PIPE,
    *,
    client_factory=Client,
    family: str = "AF_PIPE",
) -> None:
    """Authenticate and disconnect without acquiring a control lease."""
    connection = client_factory(address, family=family, authkey=authkey)
    connection.close()


def wait_for_safety_service(
    *,
    timeout: float = 15.0,
    authkey_path: Path | None = None,
    pipe_check: Callable[[], bool] = safety_pipe_available,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    process_events: Callable[[], None] | None = None,
) -> bytes:
    """Wait for both valid key material and a listening named pipe."""
    deadline = monotonic() + max(0.0, float(timeout))
    last_key_error: Exception | None = None
    while monotonic() <= deadline:
        try:
            authkey = load_authkey(authkey_path or default_authkey_path())
        except Exception as exc:
            last_key_error = exc
        else:
            if pipe_check():
                return authkey
        if process_events is not None:
            process_events()
        sleep(0.1)
    detail = f" Key status: {last_key_error}" if last_key_error else ""
    raise TimeoutError(
        "The elevated Safety Service did not become ready within "
        f"{timeout:.0f} seconds.{detail}"
    )
