from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication

import main
from services.safety_bootstrap import SafetyServiceLaunchError


class SafetyStartupFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.app = SimpleNamespace(processEvents=lambda: None)

    @patch("main.request_safety_service_setup")
    @patch("main.probe_safety_service")
    @patch("main.safety_pipe_available", return_value=True)
    @patch("main.load_authkey", return_value=b"x" * 48)
    def test_ready_service_skips_setup_prompt(
        self, _load, _pipe, probe, request_setup
    ) -> None:
        self.assertEqual(main.prepare_safety_service(self.app), b"x" * 48)
        probe.assert_called_once_with(b"x" * 48)
        request_setup.assert_not_called()

    @patch("main.QMessageBox.critical")
    @patch("main.probe_safety_service", side_effect=PermissionError("denied"))
    @patch("main.safety_pipe_available", return_value=True)
    @patch("main.load_authkey", return_value=b"x" * 48)
    def test_inaccessible_old_helper_requests_clean_restart(
        self, _load, _pipe, _probe, critical
    ) -> None:
        self.assertIsNone(main.prepare_safety_service(self.app))
        self.assertIn("restart Windows", critical.call_args.args[2])

    @patch("main.QMessageBox.clickedButton", return_value=None)
    @patch("main.QMessageBox.exec", return_value=0)
    def test_setup_prompt_constructs_with_actionable_buttons(
        self, _exec, _clicked
    ) -> None:
        self.assertFalse(main.request_safety_service_setup())

    @patch("main.launch_elevated_safety_service")
    @patch("main.request_safety_service_setup", return_value=False)
    @patch("main.load_authkey", side_effect=FileNotFoundError)
    def test_cancel_remains_fail_closed(self, _load, _request, launch) -> None:
        self.assertIsNone(main.prepare_safety_service(self.app))
        launch.assert_not_called()

    @patch("main.QProgressDialog")
    @patch("main.wait_for_safety_service", return_value=b"y" * 48)
    @patch("main.launch_elevated_safety_service")
    @patch("main.request_safety_service_setup", return_value=True)
    @patch("main.load_authkey", side_effect=FileNotFoundError)
    def test_accepted_setup_waits_for_independent_service(
        self, _load, _request, launch, wait, progress_class
    ) -> None:
        progress = Mock()
        progress_class.return_value = progress
        self.assertEqual(main.prepare_safety_service(self.app), b"y" * 48)
        launch.assert_called_once_with()
        wait.assert_called_once_with(process_events=self.app.processEvents)
        progress.show.assert_called_once_with()
        progress.close.assert_called_once_with()

    @patch("main.QMessageBox.critical")
    @patch(
        "main.launch_elevated_safety_service",
        side_effect=SafetyServiceLaunchError("UAC declined"),
    )
    @patch("main.request_safety_service_setup", return_value=True)
    @patch("main.load_authkey", side_effect=FileNotFoundError)
    def test_launch_failure_is_reported_and_remains_fail_closed(
        self, _load, _request, _launch, critical
    ) -> None:
        self.assertIsNone(main.prepare_safety_service(self.app))
        critical.assert_called_once()


if __name__ == "__main__":
    unittest.main()
