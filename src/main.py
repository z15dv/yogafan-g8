"""YogaFan G8 application entry point."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys

from PySide6.QtCore import QAbstractNativeEventFilter
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from control.controller import FanController
from ec.winring0_backend import WinRing0Backend
from services.device_compatibility import check_device_compatibility
from services.safety_service import (
    RemoteFanController,
    SafetyServiceCore,
    SafetyServiceServer,
    default_authkey_path,
    load_authkey,
    load_or_provision_authkey,
    provision_authkey,
)
from services.safety_bootstrap import (
    SafetyServiceLaunchError,
    launch_elevated_safety_service,
    probe_safety_service,
    safety_pipe_available,
    wait_for_safety_service,
)
from services.settings import AppSettings
from services.single_instance import SingleInstanceGuard
from services.telemetry_worker import TelemetryWorker
from services.windows_security import secure_file_for_current_user
from ui.main_window import MainWindow


def configure_logging() -> Path:
    directory = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "YogaFan G8"
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "YogaFanG8.log"
    handler = RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    return log_path


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def request_safety_service_setup() -> bool:
    """Ask before any elevation or service-side hardware initialization."""
    dialog = QMessageBox()
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setWindowTitle("Safety Service setup — YogaFan G8")
    dialog.setText("YogaFan G8 needs its independent Safety Service.")
    dialog.setInformativeText(
        "Set up and start an elevated background helper now? Windows will show "
        "a UAC confirmation. The helper starts in Lenovo Auto and the GUI stays "
        "unprivileged.\n\nNothing is installed for automatic startup; after a "
        "Windows restart you may be asked to start the helper again."
    )
    setup_button = dialog.addButton(
        "Set up and start", QMessageBox.ButtonRole.AcceptRole
    )
    dialog.addButton(QMessageBox.StandardButton.Cancel)
    dialog.setDefaultButton(setup_button)
    dialog.exec()
    return dialog.clickedButton() is setup_button


def prepare_safety_service(app: QApplication) -> bytes | None:
    """Return service credentials after an explicit, fail-closed startup flow."""
    try:
        authkey = load_authkey()
    except Exception:
        authkey = None
    if authkey is not None and safety_pipe_available():
        try:
            probe_safety_service(authkey)
        except Exception as exc:
            QMessageBox.critical(
                None,
                "Safety Service restart required — YogaFan G8",
                "A Safety Service process is running, but this GUI cannot "
                f"authenticate or open its pipe:\n\n{exc}\n\nClose all YogaFan G8 "
                "processes in Task Manager or restart Windows, then launch "
                "this newly built YogaFanG8.exe again. No fan-control command "
                "was sent by this check.",
            )
            return None
        return authkey
    if not request_safety_service_setup():
        return None
    try:
        launch_elevated_safety_service()
        progress = QProgressDialog(
            "Waiting for the independent Safety Service…", "", 0, 0
        )
        progress.setWindowTitle("Starting YogaFan G8")
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.show()
        try:
            return wait_for_safety_service(process_events=app.processEvents)
        finally:
            progress.close()
    except (OSError, TimeoutError, SafetyServiceLaunchError) as exc:
        QMessageBox.critical(
            None,
            "Safety Service unavailable — YogaFan G8",
            f"The Safety Service could not be started:\n\n{exc}\n\n"
            "No fan-control command was sent. See docs/SAFETY_SERVICE.md for "
            "the manual setup procedure.",
        )
        return None


def run_mock_self_test() -> int:
    """Validate the frozen package without loading WinRing0 or touching ports."""
    from ec.mock_backend import MockEcBackend
    from ec.protocol import (
        CPU_TEMPERATURE,
        FAN_1_TACH,
        FAN_2_TACH,
        FCMO,
        GPU_TEMPERATURE,
        ControlMode,
    )

    backend = MockEcBackend(
        {
            CPU_TEMPERATURE: 60,
            GPU_TEMPERATURE: 54,
            FCMO: 0,
            FAN_1_TACH: 30,
            FAN_2_TACH: 31,
        }
    )
    controller = FanController(lambda: backend)
    controller.initialize()
    sample = controller.read_telemetry()
    if (sample.cpu_c, sample.fan1_rpm, sample.fan2_rpm) != (60, 3000, 3100):
        return 2
    controller.set_mode(ControlMode.MANUAL)
    controller.set_manual_targets(3000, 3100)
    controller.shutdown()
    return 0 if backend.open_count == backend.close_count else 3


class WindowsPowerFilter(QAbstractNativeEventFilter):
    WM_QUERYENDSESSION = 0x0011
    WM_ENDSESSION = 0x0016
    WM_POWERBROADCAST = 0x0218
    PBT_APMSUSPEND = 0x0004
    PBT_APMRESUMESUSPEND = 0x0007
    PBT_APMRESUMEAUTOMATIC = 0x0012

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self.window = window

    def nativeEventFilter(self, _event_type, message):
        try:
            msg = ctypes.cast(
                int(message), ctypes.POINTER(wintypes.MSG)
            ).contents
            if msg.message == self.WM_POWERBROADCAST:
                if msg.wParam == self.PBT_APMSUSPEND:
                    self.window.prepare_suspend()
                elif msg.wParam in (
                    self.PBT_APMRESUMESUSPEND,
                    self.PBT_APMRESUMEAUTOMATIC,
                ):
                    self.window.resume_from_suspend()
            elif msg.message in (self.WM_QUERYENDSESSION, self.WM_ENDSESSION):
                self.window.request_system_shutdown()
        except Exception:
            logging.getLogger("yogafan.power").exception("Power event handling failed")
        return False, 0


def main() -> int:
    if "--mock-self-test" in sys.argv:
        return run_mock_self_test()
    configure_logging()
    logging.info("YogaFan G8 application startup")
    if "--provision-service-key" in sys.argv:
        if not is_admin():
            logging.error("Provisioning the Safety Service key requires elevation")
            return 1
        try:
            key_path = default_authkey_path()
            provision_authkey(key_path)
            secure_file_for_current_user(key_path)
        except FileExistsError:
            logging.error("Safety Service key already exists; refusing to overwrite it")
            return 2
        except Exception:
            logging.exception("Safety Service key provisioning failed")
            return 3
        logging.info("Safety Service key provisioned; Windows ACL review is still required")
        return 0
    if "--safety-service" in sys.argv:
        if not is_admin():
            logging.error("Safety Service requires an elevated SCM account")
            return 1
        compatibility = check_device_compatibility()
        if not compatibility.supported:
            logging.error("Safety Service refused hardware: %s", compatibility.reason)
            return 2
        try:
            authkey = load_or_provision_authkey(
                provision_if_missing="--provision-if-missing" in sys.argv
            )
            secure_file_for_current_user(default_authkey_path())
            core = SafetyServiceCore(FanController(WinRing0Backend))
            SafetyServiceServer(core, authkey).serve_forever()
        except Exception:
            logging.exception("Safety Service terminated")
            return 3
        return 0
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("YogaFan.G8")
    except Exception:
        logging.getLogger("yogafan.ui").debug("Could not set Windows AppUserModelID")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("YogaFan G8")
    app.setOrganizationName("YogaFan G8")

    single_instance = SingleInstanceGuard(parent=app)
    if not single_instance.claim_or_notify():
        logging.info("Existing YogaFan G8 GUI notified; second launch exiting")
        return 0

    compatibility = check_device_compatibility()
    if not compatibility.supported:
        QMessageBox.critical(
            None,
            "Unsupported device — YogaFan G8",
            compatibility.reason
            + "\n\nNo embedded-controller commands were attempted.",
        )
        single_instance.close()
        return 1

    authkey = prepare_safety_service(app)
    if authkey is None:
        single_instance.close()
        return 2
    controller = RemoteFanController(authkey)
    worker = TelemetryWorker(
        controller,
        nvml_fallback_enabled=AppSettings().load()["nvml_fallback"],
    )
    window = MainWindow(worker)
    single_instance.attach_window(window)
    window.backend_label.setText("Backend: Safety Service / WinRing0")
    power_filter = WindowsPowerFilter(window)
    app.installNativeEventFilter(power_filter)

    old_hook = sys.excepthook

    def exception_hook(exc_type, exc, traceback) -> None:
        logging.getLogger("yogafan").critical(
            "Unhandled exception", exc_info=(exc_type, exc, traceback)
        )
        worker.stop()
        worker.wait(7000)
        old_hook(exc_type, exc, traceback)

    sys.excepthook = exception_hook
    window.show()
    worker.start()
    result = app.exec()
    if worker.isRunning():
        worker.stop()
        worker.wait(7000)
    single_instance.close()
    logging.info("YogaFan G8 application shutdown")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
