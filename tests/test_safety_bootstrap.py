from __future__ import annotations

import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from services.safety_bootstrap import (
    SafetyServiceLaunchError,
    elevated_service_command,
    launch_elevated_safety_service,
    probe_safety_service,
    safety_pipe_available,
    wait_for_safety_service,
)
from services.safety_service import load_or_provision_authkey


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        self.value += 0.01
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class SafetyBootstrapTests(unittest.TestCase):
    @staticmethod
    def _temporary_directory():
        root = Path.cwd() / ".test-output"
        root.mkdir(exist_ok=True)
        return tempfile.TemporaryDirectory(dir=root)

    def test_frozen_command_reuses_executable(self) -> None:
        program, arguments, working_directory = elevated_service_command(
            executable=r"C:\YogaFan\YogaFanG8.exe", frozen=True
        )
        self.assertEqual(program, r"C:\YogaFan\YogaFanG8.exe")
        self.assertEqual(
            arguments, ["--safety-service", "--provision-if-missing"]
        )
        self.assertEqual(working_directory, r"C:\YogaFan")

    def test_source_command_includes_main_script(self) -> None:
        script = Path(r"C:\src\main.py")
        _, arguments, _ = elevated_service_command(
            executable=r"C:\Python\python.exe", main_script=script, frozen=False
        )
        self.assertEqual(arguments[0], str(script))
        self.assertIn("--provision-if-missing", arguments)

    @patch("services.safety_bootstrap.sys.platform", "win32")
    def test_elevation_failure_is_reported(self) -> None:
        with self.assertRaises(SafetyServiceLaunchError):
            launch_elevated_safety_service(
                shell_execute=lambda *_args: 5,
                executable=r"C:\YogaFan\YogaFanG8.exe",
                frozen=True,
            )

    def test_explicit_provision_creates_random_key_once(self) -> None:
        with self._temporary_directory() as directory:
            path = Path(directory) / "service.auth"
            first = load_or_provision_authkey(
                path=path, provision_if_missing=True
            )
            second = load_or_provision_authkey(
                path=path, provision_if_missing=True
            )
            self.assertEqual(first, second)
            self.assertEqual(len(first), 48)

    def test_wait_requires_key_and_pipe(self) -> None:
        with self._temporary_directory() as directory:
            path = Path(directory) / "service.auth"
            key = load_or_provision_authkey(path=path, provision_if_missing=True)
            checks = iter((False, False, True))
            clock = Clock()
            result = wait_for_safety_service(
                timeout=2,
                authkey_path=path,
                pipe_check=lambda: next(checks),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
            self.assertEqual(result, key)

    @patch("services.safety_bootstrap.sys.platform", "win32")
    @patch(
        "services.safety_bootstrap.ctypes.windll.kernel32.WaitNamedPipeW",
        return_value=1,
    )
    def test_pipe_readiness_uses_windows_wait_named_pipe(self, wait_pipe) -> None:
        address = r"\\.\pipe\YogaFanG8-Test"
        self.assertTrue(safety_pipe_available(address))
        wait_pipe.assert_called_once_with(address, 0)

    def test_probe_authenticates_without_sending_a_command(self) -> None:
        class Connection:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        connection = Connection()
        calls = []

        def client_factory(address, *, family, authkey):
            calls.append((address, family, authkey))
            return connection

        probe_safety_service(b"x" * 48, client_factory=client_factory)
        self.assertTrue(connection.closed)
        self.assertEqual(calls[0][1:], ("AF_PIPE", b"x" * 48))


if __name__ == "__main__":
    unittest.main()
