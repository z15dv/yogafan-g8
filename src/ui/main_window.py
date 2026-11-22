"""Main YogaFan G8 window."""

from __future__ import annotations

import ctypes
import logging
from pathlib import Path
import sys

from PySide6.QtCore import (
    QEasingCurve,
    QParallelAnimationGroup,
    QPropertyAnimation,
    QSignalBlocker,
    QSize,
    QTimer,
    QUrl,
    Qt,
)
from PySide6.QtGui import QAction, QCloseEvent, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from ec.protocol import ControlMode
from control.fan_curve import CurveProfile
from control.safety import ThermalProtectionPolicy
from services.notifications import (
    TemperatureWarning,
    TemperatureWarningTracker,
    WarningLevel,
    show_temperature_notification,
)
from services.settings import AppSettings
from services.telemetry_logger import TelemetryCsvLogger, default_log_directory
from services.telemetry_worker import TelemetryWorker
from .curve_editor import CurveEditor, NoWheelSpinBox
from .fan_widget import FanWidget
from .telemetry_card import TelemetryCard


STYLE = """
QWidget { background:#0a1018; color:#dce7f3; font:10pt 'Segoe UI'; }
QMainWindow, QScrollArea, QScrollArea > QWidget > QWidget {
    background:#0a1018;
}
QWidget#appContent {
    background:qradialgradient(cx:0.38,cy:0.12,radius:1.05,fx:0.38,fy:0.12,stop:0 #101d2a,stop:0.45 #0b141e,stop:1 #080e15);
}
QLabel { background:transparent; }
QFrame#card, QFrame#telemetryCard, QFrame#fanCard {
    background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #142130,stop:1 #0e1925);
    border:1px solid #29445e; border-radius:13px;
}
QFrame#card:hover, QFrame#telemetryCard:hover, QFrame#fanCard:hover { background:#152534; border-color:#37617e; }
QFrame#telemetryIconTile { background:#132437; border:1px solid #284c69; border-radius:16px; }
QLabel#title { font-size:34pt; font-weight:700; color:#f7f9fc; }
QLabel#subtitle, QLabel#secondary { color:#94a9c9; }
QLabel#sectionTitle { color:#9eb4d5; font-size:10pt; font-weight:700; letter-spacing:1px; }
QLabel#cardTitle { color:#bfd0e1; font-size:11pt; font-weight:600; }
QLabel#telemetryCaption { color:#a9bde0; font-size:11pt; }
QLabel#telemetryValue { color:#5bd9ff; font-size:27pt; font-weight:700; }
QLabel#fanTitle { color:#f4f7fb; font-size:14pt; font-weight:700; }
QLabel#fanTarget { color:#a9bde0; font-size:10pt; }
QLabel#fanActualValue { color:#5bd9ff; font-size:31pt; font-weight:700; }
QLabel#fanActualUnit { color:#55cff4; font-size:13pt; font-weight:700; padding-bottom:5px; }
QLabel#sliderLimit { color:#a9bde0; font-size:9pt; }
QLabel#connectionStatus { color:#3ce5aa; font-size:11pt; }
QLabel#backendStatus { color:#9eb3d5; font-size:10pt; }
QLabel#safetyStatus { color:#f2bb62; font-size:10pt; }
QLabel#warning { background:#8b2331; color:#fff4f5; border:1px solid #d55265; border-radius:9px; padding:10px; font-size:12pt; font-weight:700; }
QPushButton { background:#152335; border:1px solid #2d4a65; border-radius:10px; padding:12px 16px; font-size:11pt; font-weight:500; }
QPushButton:hover { background:#1d3044; border-color:#3a5873; }
QPushButton:focus { border-color:#55d6ff; outline:none; }
QPushButton:pressed { background:#101b27; }
QPushButton:checked, QPushButton#primaryButton { background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #0794cf,stop:1 #0aaed2); border-color:#57e3ff; color:white; }
QPushButton#primaryButton:hover { background:#0993bd; }
QPushButton#secondaryButton { background:#111b27; }
QPushButton#compactButton { padding:7px 12px; font-size:9pt; border-radius:7px; }
QPushButton#profileButton { padding:9px 12px; font-size:10pt; }
QPushButton:disabled { color:#536477; background:#101721; border-color:#1c2936; }
QCheckBox {
    background:transparent; border:none; outline:none;
    spacing:10px; color:#eef4fb; padding:6px 2px; font-size:11pt;
}
QCheckBox:hover, QCheckBox:focus, QCheckBox:pressed, QCheckBox:disabled {
    background:transparent; border:none; outline:none;
}
QCheckBox::indicator { width:21px; height:21px; border:1px solid #46627d; border-radius:5px; background:#0c141e; }
QCheckBox::indicator:hover { border-color:#55d6ff; }
QCheckBox::indicator:checked { background:#0788b1; border-color:#62dcff; image:url(__CHECK_ICON__); }
QCheckBox::indicator:disabled { background:#101721; border-color:#293746; }
QSlider {
    background-color:rgba(0, 0, 0, 0); border:none; outline:none;
    min-height:24px; margin:0; padding:0;
}
QSlider:hover, QSlider:pressed, QSlider:focus, QSlider:disabled {
    background-color:rgba(0, 0, 0, 0); border:none; outline:none;
}
QSlider::groove:horizontal {
    height:8px; margin:0;
    background-color:#1d3042; border:1px solid #36546d; border-radius:4px;
}
QSlider::groove:horizontal:hover {
    background-color:#21384b; border-color:#42657f;
}
QSlider::groove:horizontal:pressed {
    background-color:#192b3b; border-color:#3e6079;
}
QSlider::groove:horizontal:focus {
    background-color:#1d3042; border-color:#47809c;
}
QSlider::groove:horizontal:disabled {
    background-color:#182632; border-color:#273a4a;
}
QSlider::add-page:horizontal {
    background-color:#2a4155; border:1px solid #36546d; border-radius:4px;
}
QSlider::add-page:horizontal:hover {
    background-color:#304a60; border-color:#42657f;
}
QSlider::add-page:horizontal:pressed {
    background-color:#263d50; border-color:#3e6079;
}
QSlider::add-page:horizontal:focus {
    background-color:#2a4155; border-color:#47809c;
}
QSlider::add-page:horizontal:disabled {
    background-color:#203342; border-color:#293e50;
}
QSlider::sub-page:horizontal {
    background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #00a9d8,stop:1 #23d9f5);
    border:1px solid #23bddd; border-radius:4px;
}
QSlider::sub-page:horizontal:hover {
    background-color:#16c9ec; border-color:#5ee7ff;
}
QSlider::sub-page:horizontal:pressed {
    background-color:#079fc8; border-color:#28cae8;
}
QSlider::sub-page:horizontal:focus {
    background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #00a9d8,stop:1 #23d9f5);
    border-color:#72e8ff;
}
QSlider::sub-page:horizontal:disabled {
    background-color:#35586b; border-color:#3d6173;
}
QSlider::handle:horizontal {
    width:17px; margin:-7px 0; border-radius:10px;
    background-color:#d8f6ff; border:2px solid #8ee7fb;
}
QSlider::handle:horizontal:hover {
    background-color:#79e2fb; border-color:#c2f5ff;
}
QSlider::handle:horizontal:pressed {
    background-color:#39c9eb; border-color:#ecfbff;
}
QSlider::handle:horizontal:focus {
    background-color:#afefff; border-color:#55d6ff;
}
QSlider::handle:horizontal:disabled {
    background-color:#526d80; border-color:#6f899a;
}
QTableWidget { background:#0c141e; alternate-background-color:#0f1924; border:1px solid #26384b; border-radius:8px; gridline-color:#1c2a39; outline:none; }
QTableWidget::item { background:#0c141e; }
QHeaderView, QHeaderView::viewport { background:#111c29; }
QHeaderView::section { background:#172536; color:#a9bacb; padding:8px; border:0; border-bottom:1px solid #2a3c50; font-weight:600; }
QTableCornerButton::section { background:#172536; border:0; }
QTabWidget::pane { background:#0b141e; border:1px solid #263b50; border-radius:8px; top:-1px; }
QTabBar::tab { background:#101b28; color:#91a9c5; border:1px solid #263b50; padding:9px 22px; min-width:110px; }
QTabBar::tab:first { border-top-left-radius:7px; }
QTabBar::tab:last { border-top-right-radius:7px; }
QTabBar::tab:selected { background:#143047; color:#5cddff; border-color:#2f7895; }
QSpinBox { background:#0c141e; color:#dce7f3; border:1px solid #2d4055; border-radius:7px; padding:7px 9px; selection-background-color:#087fa7; }
QSpinBox:hover, QSpinBox:focus { border-color:#4c89a5; background:#101b27; }
QSpinBox[invalid="true"] { color:#ff9aa7; border:1px solid #e6576b; background:#29151c; }
QLabel[invalid="true"] { color:#ff7485; }
QLabel#settingHint { color:#8ea5bf; font-size:9pt; padding-left:34px; }
QLabel#settingValueLabel { color:#b7c8da; font-size:10pt; }
QScrollBar:vertical { background:#0d151f; width:10px; margin:2px; border-radius:5px; }
QScrollBar::handle:vertical { background:#30465b; min-height:32px; border-radius:4px; }
QScrollBar::handle:vertical:hover { background:#3d5d76; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QToolTip { background:#172536; color:#e4edf5; border:1px solid #3a536d; padding:6px; }
"""


def resource_path(relative: str) -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return root / relative


LENOVO_MODE_ICON_ASSETS = {
    0: "assets/modes/intelligent.png",
    1: "assets/modes/performance.png",
    2: "assets/leaf.svg",
    3: "assets/modes/unknown.svg",
    4: "assets/modes/unknown.svg",
}


def lenovo_mode_icon_asset(fcmo_raw: int) -> str:
    """Return a display-only asset; unknown FCMO values stay neutral."""
    return LENOVO_MODE_ICON_ASSETS.get(int(fcmo_raw), "assets/modes/unknown.svg")


def format_gpu_temperature(temperature_c: int) -> tuple[str, str]:
    if temperature_c <= 0 or temperature_c > 125:
        return "N/A", "GPU sensor unavailable or GPU is sleeping."
    return f"{temperature_c} °C", ""


class MainWindow(QMainWindow):
    def __init__(self, worker: TelemetryWorker) -> None:
        super().__init__()
        self.worker = worker
        self.settings = AppSettings()
        self.warning_tracker = TemperatureWarningTracker()
        self.telemetry_logger = TelemetryCsvLogger()
        self.log = logging.getLogger("yogafan.ui")
        self._closing = False
        self._session_ending = False
        self._connected = False
        self._protection_restore_confirmed: bool | None = None
        self._dark_titlebar_applied = False
        self.current_mode = ControlMode.LENOVO_AUTO
        self.current_profile = CurveProfile.BALANCED
        self._curve_visible = False
        self._warning_visible = False
        self._thermal_warning_acknowledged = False
        self._accepted_thermal_settings = (True, 95, 85)
        self._mode_animations: dict[ControlMode, QPropertyAnimation] = {}
        self._mode_effects: dict[ControlMode, QGraphicsOpacityEffect] = {}
        self.app_icon = QIcon(str(resource_path("assets/yogafan-g8.ico")))
        self.setWindowIcon(self.app_icon)
        if QApplication.instance() is not None:
            QApplication.instance().setWindowIcon(self.app_icon)
        self.setWindowTitle("YogaFan G8")
        self.setMinimumSize(760, 600)
        self.resize(1120, 800)
        check_icon = resource_path("assets/check.svg").as_posix()
        self.setStyleSheet(STYLE.replace("__CHECK_ICON__", check_icon))
        self._build_ui()
        self._load_settings()
        self._connect_settings_controls()
        self._connect_worker()
        self._create_tray()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._dark_titlebar_applied or sys.platform != "win32":
            return
        self._dark_titlebar_applied = True
        try:
            enabled = ctypes.c_int(1)
            hwnd = ctypes.c_void_p(int(self.winId()))
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(enabled), ctypes.sizeof(enabled)
            )
            if result:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 19, ctypes.byref(enabled), ctypes.sizeof(enabled)
                )
        except Exception:
            self.log.debug("Native dark title bar is unavailable", exc_info=True)

    def _build_ui(self) -> None:
        content = QWidget()
        content.setObjectName("appContent")
        outer = QVBoxLayout(content)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(15)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setWidget(content)

        header = QHBoxLayout()
        header.setSpacing(18)
        brand_mark = QLabel()
        brand_mark.setPixmap(
            QIcon(str(resource_path("assets/yogafan-g8.png"))).pixmap(74, 74)
        )
        brand_mark.setFixedSize(82, 82)
        brand_mark.setAlignment(Qt.AlignCenter)
        header.addWidget(brand_mark)
        name = QVBoxLayout()
        name.setSpacing(2)
        name.addStretch(1)
        title = QLabel("YogaFan G8")
        title.setObjectName("title")
        title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        name.addWidget(title)
        subtitle = QLabel("Lenovo Yoga Pro 9 14IRP8 · machine type 83BU")
        subtitle.setObjectName("subtitle")
        subtitle.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        name.addWidget(subtitle)
        name.addStretch(1)
        header.addLayout(name, 3)
        status_box = QVBoxLayout()
        status_box.setSpacing(12)
        status_box.addStretch(1)
        self.ec_status = QLabel("EC status: Connecting…")
        self.ec_status.setObjectName("connectionStatus")
        self.ec_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.backend_label = QLabel("Backend: WinRing0")
        self.backend_label.setObjectName("backendStatus")
        self.backend_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.safety_status = QLabel("Safety Service: Connecting…")
        self.safety_status.setObjectName("safetyStatus")
        self.safety_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        status_box.addWidget(self.ec_status, alignment=Qt.AlignRight)
        status_box.addWidget(self.safety_status, alignment=Qt.AlignRight)
        status_box.addWidget(self.backend_label, alignment=Qt.AlignRight)
        status_box.addStretch(1)
        header.addLayout(status_box, 1)
        outer.addLayout(header)

        self.warning = QLabel("")
        self.warning.setObjectName("warning")
        self.warning.setAlignment(Qt.AlignCenter)
        self.warning.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.warning_effect = QGraphicsOpacityEffect(self.warning)
        self.warning.setGraphicsEffect(self.warning_effect)
        self.warning_effect.setOpacity(0.0)
        self.warning.setMaximumHeight(0)
        self.warning.hide()
        outer.addWidget(self.warning)

        self.telemetry_layout = QGridLayout()
        self.telemetry_layout.setHorizontalSpacing(14)
        self.telemetry_layout.setVerticalSpacing(14)
        self.cpu_card = TelemetryCard(
            "CPU temperature", str(resource_path("assets/cpu.svg"))
        )
        self.gpu_card = TelemetryCard(
            "GPU temperature", str(resource_path("assets/gpu.svg"))
        )
        self.mode_card = TelemetryCard(
            "Lenovo Mode", str(resource_path(lenovo_mode_icon_asset(0)))
        )
        self._compact_telemetry_layout: bool | None = None
        outer.addLayout(self.telemetry_layout)
        self._apply_telemetry_layout(False)
        self.cpu_value = self.cpu_card.value
        self.gpu_value = self.gpu_card.value
        self.lenovo_mode = self.mode_card.value

        mode_heading = QLabel("FAN CONTROL")
        mode_heading.setObjectName("sectionTitle")
        outer.addWidget(mode_heading)
        modes = QHBoxLayout()
        modes.setSpacing(10)
        self.mode_group = QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_buttons: dict[ControlMode, QPushButton] = {}
        mode_icons = {
            ControlMode.LENOVO_AUTO: "assets/mode-auto.svg",
            ControlMode.MANUAL: "assets/mode-manual.svg",
            ControlMode.FAN_CURVES: "assets/mode-curve.svg",
        }
        for mode in ControlMode:
            button = QPushButton(mode.value)
            button.setCheckable(True)
            button.setIcon(QIcon(str(resource_path(mode_icons[mode]))))
            button.setIconSize(QSize(22, 22))
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            modes.addWidget(button)
            button.clicked.connect(lambda checked=False, m=mode: self._select_mode(m))
        self.mode_buttons[ControlMode.LENOVO_AUTO].setChecked(True)
        outer.addLayout(modes)

        self.profile_panel = QWidget()
        profile_layout = QHBoxLayout(self.profile_panel)
        profile_layout.setContentsMargins(0, 0, 0, 0)
        profile_layout.setSpacing(8)
        profile_label = QLabel("CURVE PROFILE")
        profile_label.setObjectName("sectionTitle")
        profile_layout.addWidget(profile_label)
        self.profile_group = QButtonGroup(self)
        self.profile_group.setExclusive(True)
        self.profile_buttons: dict[CurveProfile, QPushButton] = {}
        for profile in CurveProfile:
            button = QPushButton(profile.value)
            button.setObjectName("profileButton")
            button.setCheckable(True)
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            button.clicked.connect(
                lambda checked=False, p=profile: self._select_profile(p)
            )
            self.profile_group.addButton(button)
            self.profile_buttons[profile] = button
            profile_layout.addWidget(button, 1)
        self.profile_buttons[CurveProfile.BALANCED].setChecked(True)
        self.profile_panel.hide()
        outer.addWidget(self.profile_panel)

        fan_heading = QLabel("FANS")
        fan_heading.setObjectName("sectionTitle")
        outer.addWidget(fan_heading)
        self.fans_layout = QGridLayout()
        self.fans_layout.setHorizontalSpacing(14)
        self.fans_layout.setVerticalSpacing(14)
        self.fan1 = FanWidget("Fan 1")
        self.fan2 = FanWidget("Fan 2")
        self._compact_fan_layout: bool | None = None
        outer.addLayout(self.fans_layout)
        self._apply_fan_layout(False)
        self.link_fans = QCheckBox("Link Fans")
        self.link_fans.setChecked(True)
        outer.addWidget(self.link_fans)

        self.curve_editor = CurveEditor()
        self.curve_effect = QGraphicsOpacityEffect(self.curve_editor)
        self.curve_editor.setGraphicsEffect(self.curve_effect)
        self.curve_effect.setOpacity(0.0)
        self.curve_editor.setMaximumHeight(0)
        self.curve_editor.hide()
        outer.addWidget(self.curve_editor)

        settings_heading = QLabel("SETTINGS")
        settings_heading.setObjectName("sectionTitle")
        outer.addWidget(settings_heading)

        settings_card = QFrame()
        settings_card.setObjectName("card")
        settings_layout = QGridLayout(settings_card)
        settings_layout.setContentsMargins(12, 12, 12, 12)
        settings_layout.setHorizontalSpacing(8)
        settings_layout.setVerticalSpacing(3)

        self.run_in_background = QCheckBox(
            "Run in background when the window is closed"
        )
        self.run_in_background.setChecked(True)
        self.run_in_background.setMinimumWidth(0)
        self.run_in_background.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Fixed
        )
        settings_layout.addWidget(self.run_in_background, 0, 0, 1, 4)
        background_hint = QLabel(
            "On: X hides YogaFan G8 to the tray. Off: X exits and restores Lenovo Auto."
        )
        background_hint.setObjectName("settingHint")
        background_hint.setWordWrap(True)
        settings_layout.addWidget(background_hint, 1, 0, 1, 4)

        self.nvml_fallback = QCheckBox("Power-aware NVIDIA fallback")
        self.nvml_fallback.setMinimumWidth(0)
        self.nvml_fallback.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.nvml_fallback.setToolTip(
            "Uses NVIDIA NVML only when the EC cannot provide a valid GPU "
            "temperature. Queries are throttled to reduce unnecessary dGPU wake-ups."
        )
        settings_layout.addWidget(self.nvml_fallback, 2, 0, 1, 4)
        nvml_hint = QLabel(
            "Reads GPU temperature through NVIDIA NVML only if the EC value is unavailable."
        )
        nvml_hint.setObjectName("settingHint")
        nvml_hint.setWordWrap(True)
        settings_layout.addWidget(nvml_hint, 3, 0, 1, 4)

        self.thermal_protection = QCheckBox(
            "Automatic thermal protection (recommended)"
        )
        self.thermal_protection.setChecked(True)
        self.thermal_protection.setMinimumWidth(0)
        self.thermal_protection.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Fixed
        )
        settings_layout.addWidget(self.thermal_protection, 4, 0, 1, 4)
        thermal_hint = QLabel(
            "At the limits below, YogaFan switches fan control to Lenovo Auto. "
            "It does not shut down Windows."
        )
        thermal_hint.setObjectName("settingHint")
        thermal_hint.setWordWrap(True)
        settings_layout.addWidget(thermal_hint, 5, 0, 1, 4)

        cpu_limit_label = QLabel("CPU limit")
        cpu_limit_label.setObjectName("settingValueLabel")
        self.cpu_critical_limit = NoWheelSpinBox()
        self.cpu_critical_limit.setRange(
            ThermalProtectionPolicy.CPU_CRITICAL_MIN_C,
            ThermalProtectionPolicy.CPU_CRITICAL_MAX_C,
        )
        self.cpu_critical_limit.setValue(ThermalProtectionPolicy.CPU_CRITICAL_C)
        self.cpu_critical_limit.setSuffix(" °C")
        self.cpu_critical_limit.setFocusPolicy(Qt.StrongFocus)
        self.cpu_critical_limit.setMinimumWidth(0)
        self.cpu_critical_limit.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Fixed
        )
        gpu_limit_label = QLabel("GPU limit")
        gpu_limit_label.setObjectName("settingValueLabel")
        self.gpu_critical_limit = NoWheelSpinBox()
        self.gpu_critical_limit.setRange(
            ThermalProtectionPolicy.GPU_CRITICAL_MIN_C,
            ThermalProtectionPolicy.GPU_CRITICAL_MAX_C,
        )
        self.gpu_critical_limit.setValue(ThermalProtectionPolicy.GPU_CRITICAL_C)
        self.gpu_critical_limit.setSuffix(" °C")
        self.gpu_critical_limit.setFocusPolicy(Qt.StrongFocus)
        self.gpu_critical_limit.setMinimumWidth(0)
        self.gpu_critical_limit.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Fixed
        )
        settings_layout.addWidget(cpu_limit_label, 6, 0)
        settings_layout.addWidget(self.cpu_critical_limit, 6, 1, 1, 3)
        settings_layout.addWidget(gpu_limit_label, 7, 0)
        settings_layout.addWidget(self.gpu_critical_limit, 7, 1, 1, 3)
        settings_layout.setColumnStretch(1, 1)
        outer.addWidget(settings_card)

        logging_card = QFrame()
        logging_card.setObjectName("card")
        logging_layout = QGridLayout(logging_card)
        logging_layout.setContentsMargins(16, 12, 16, 12)
        logging_layout.setHorizontalSpacing(8)
        logging_layout.setVerticalSpacing(8)
        self.logging_status = QLabel("CSV telemetry logging is off")
        self.logging_status.setObjectName("secondary")
        self.logging_status.setMinimumWidth(0)
        self.logging_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        logging_layout.addWidget(self.logging_status, 0, 0, 1, 2)
        self.start_logging_button = QPushButton("Start Logging")
        self.start_logging_button.setObjectName("compactButton")
        self.stop_logging_button = QPushButton("Stop Logging")
        self.stop_logging_button.setObjectName("compactButton")
        self.open_log_button = QPushButton("Open Log Folder")
        self.open_log_button.setObjectName("compactButton")
        self.export_log_button = QPushButton("Export CSV")
        self.export_log_button.setObjectName("compactButton")
        for index, button in enumerate((
            self.start_logging_button,
            self.stop_logging_button,
            self.open_log_button,
            self.export_log_button,
        )):
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            logging_layout.addWidget(button, 1 + index // 2, index % 2)
        logging_layout.setColumnStretch(0, 1)
        logging_layout.setColumnStretch(1, 1)
        self.stop_logging_button.setEnabled(False)
        self.export_log_button.setEnabled(False)
        self.start_logging_button.clicked.connect(self._start_logging)
        self.stop_logging_button.clicked.connect(self._stop_logging)
        self.open_log_button.clicked.connect(self._open_log_folder)
        self.export_log_button.clicked.connect(self._export_log)
        outer.addWidget(logging_card)

        self.message = QLabel("Starting in Lenovo Auto…")
        self.message.setObjectName("secondary")
        outer.addWidget(self.message)
        outer.addStretch(1)
        self.setCentralWidget(self.scroll_area)

        self.fan1.target_changed.connect(lambda value: self._manual_changed(1, value))
        self.fan2.target_changed.connect(lambda value: self._manual_changed(2, value))
        self.curve_editor.apply_requested.connect(self.worker.request_curve)
        self._set_manual_enabled(False)
        self.mode_buttons[ControlMode.MANUAL].setEnabled(False)
        self.mode_buttons[ControlMode.FAN_CURVES].setEnabled(False)
        for button in self.profile_buttons.values():
            button.setEnabled(False)

    def _connect_worker(self) -> None:
        self.worker.telemetry.connect(self._update_telemetry)
        self.worker.connection_changed.connect(self._connection_changed)
        self.worker.mode_changed.connect(self._mode_changed)
        self.worker.targets_changed.connect(self._targets_changed)
        self.worker.manual_initialized.connect(self._manual_initialized)
        self.worker.profile_changed.connect(self._profile_changed)
        self.worker.protection_triggered.connect(self._protection_triggered)
        self.worker.operation_error.connect(self._show_error)

    def _connect_settings_controls(self) -> None:
        self.run_in_background.toggled.connect(self._background_setting_changed)
        self.nvml_fallback.toggled.connect(self._nvml_setting_changed)
        # ``clicked`` runs after the mouse release.  A modal warning opened from
        # ``toggled`` can otherwise cause the original click to toggle the box a
        # second time when the dialog closes.
        self.thermal_protection.clicked.connect(self._thermal_settings_changed)
        self.cpu_critical_limit.editingFinished.connect(
            self._thermal_settings_changed
        )
        self.gpu_critical_limit.editingFinished.connect(
            self._thermal_settings_changed
        )

    def _create_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.app_icon, self)
        self.tray.setToolTip("YogaFan G8")
        menu = self.tray.contextMenu()
        if menu is None:
            from PySide6.QtWidgets import QMenu

            menu = QMenu()
            show_action = QAction("Open YogaFan G8", menu)
            show_action.triggered.connect(self._show_from_tray)
            auto_action = QAction("Restore Lenovo Auto", menu)
            auto_action.triggered.connect(
                lambda: self.worker.request_mode(ControlMode.LENOVO_AUTO)
            )
            quit_action = QAction("Exit", menu)
            quit_action.triggered.connect(self.exit_application)
            menu.addAction(show_action)
            menu.addAction(auto_action)
            menu.addSeparator()
            menu.addAction(quit_action)
            self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _load_settings(self) -> None:
        values = self.settings.load()
        if values["geometry"]:
            self.restoreGeometry(values["geometry"])
        if values["state"]:
            self.restoreState(values["state"])
        self.link_fans.setChecked(values["link_fans"])
        self.nvml_fallback.setChecked(values.get("nvml_fallback", True))
        self.run_in_background.setChecked(values.get("run_in_background", True))
        thermal_enabled = values.get("thermal_protection_enabled", True)
        cpu_critical_c = values.get("cpu_critical_c", 95)
        gpu_critical_c = values.get("gpu_critical_c", 85)
        self.thermal_protection.setChecked(thermal_enabled)
        self.cpu_critical_limit.setValue(cpu_critical_c)
        self.gpu_critical_limit.setValue(gpu_critical_c)
        self._accepted_thermal_settings = (
            thermal_enabled,
            cpu_critical_c,
            gpu_critical_c,
        )
        self.warning_tracker.configure(cpu_critical_c, gpu_critical_c)
        self.worker.request_thermal_protection(
            thermal_enabled,
            cpu_critical_c,
            gpu_critical_c,
        )
        if not thermal_enabled:
            self._set_warning(
                "AUTOMATIC THERMAL PROTECTION DISABLED · custom fan control "
                "will not switch to Lenovo Auto at a temperature limit",
                animated=False,
            )
        self.fan1.set_visual_rpm(values["fan1_rpm"], animated=False)
        self.fan2.set_visual_rpm(values["fan2_rpm"], animated=False)
        try:
            self.curve_editor.set_curves(
                values.get("custom_cpu_curve", values["curve"]),
                values.get("custom_gpu_curve", self.curve_editor.gpu_points()),
            )
            self.worker.configure_custom_curves(
                self.curve_editor.applied_points(),
                self.curve_editor.applied_gpu_points(),
            )
        except ValueError:
            pass
        self.current_profile = values.get("curve_profile", CurveProfile.BALANCED)
        self.profile_buttons[self.current_profile].setChecked(True)
        self.worker.configure_profile(self.current_profile)

    def _save_settings(self) -> None:
        self.settings.save(
            {
                "window/geometry": self.saveGeometry(),
                "window/state": self.saveState(),
                "fans/link": self.link_fans.isChecked(),
                "fans/manual1": self.fan1.slider.value(),
                "fans/manual2": self.fan2.slider.value(),
                "curves/custom_cpu": [
                    list(point) for point in self.curve_editor.applied_points()
                ],
                "curves/custom_gpu": [
                    list(point) for point in self.curve_editor.applied_gpu_points()
                ],
                "curves/selected_profile": self.current_profile.value,
                "sensors/nvml_fallback": self.nvml_fallback.isChecked(),
                "app/run_in_background": self.run_in_background.isChecked(),
                "safety/thermal_protection_enabled": (
                    self.thermal_protection.isChecked()
                ),
                "safety/cpu_critical_c": self.cpu_critical_limit.value(),
                "safety/gpu_critical_c": self.gpu_critical_limit.value(),
            }
        )

    def _background_setting_changed(self, _enabled: bool) -> None:
        self._save_settings()

    def _nvml_setting_changed(self, enabled: bool) -> None:
        self.worker.request_nvml_fallback(enabled)
        self._save_settings()

    def _restore_accepted_thermal_settings(self) -> None:
        enabled, cpu_critical_c, gpu_critical_c = self._accepted_thermal_settings
        blockers = (
            QSignalBlocker(self.thermal_protection),
            QSignalBlocker(self.cpu_critical_limit),
            QSignalBlocker(self.gpu_critical_limit),
        )
        self.thermal_protection.setChecked(enabled)
        self.cpu_critical_limit.setValue(cpu_critical_c)
        self.gpu_critical_limit.setValue(gpu_critical_c)
        del blockers

    def _thermal_settings_changed(self, _checked: bool | None = None) -> None:
        candidate = (
            self.thermal_protection.isChecked(),
            self.cpu_critical_limit.value(),
            self.gpu_critical_limit.value(),
        )
        if candidate == self._accepted_thermal_settings:
            return
        if not self._thermal_warning_acknowledged:
            result = QMessageBox.warning(
                self,
                "Thermal protection settings — YogaFan G8",
                "Changing these limits can reduce hardware protection. At the "
                "configured threshold YogaFan normally restores Lenovo Auto; it "
                "does not shut down Windows.\n\nDisabling the option removes this "
                "automatic temperature handoff while custom fan control is active. "
                "Continue only if you understand the risk.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if result != QMessageBox.StandardButton.Yes:
                self._restore_accepted_thermal_settings()
                return
            self._thermal_warning_acknowledged = True
        self._accepted_thermal_settings = candidate
        enabled, cpu_critical_c, gpu_critical_c = candidate
        self.warning_tracker.configure(cpu_critical_c, gpu_critical_c)
        self.worker.request_thermal_protection(
            enabled,
            cpu_critical_c,
            gpu_critical_c,
        )
        self._set_warning(
            None
            if enabled
            else "AUTOMATIC THERMAL PROTECTION DISABLED · custom fan control "
            "will not switch to Lenovo Auto at a temperature limit"
        )
        state = "on" if enabled else "OFF"
        self.message.setText(
            f"Thermal protection {state} · CPU {cpu_critical_c} °C · "
            f"GPU {gpu_critical_c} °C"
        )
        self._save_settings()

    def _select_mode(self, mode: ControlMode) -> None:
        self.worker.request_mode(mode, self.link_fans.isChecked())
        if mode is ControlMode.FAN_CURVES:
            self.worker.request_profile(self.current_profile)
        self.profile_panel.setVisible(mode is ControlMode.FAN_CURVES)
        self._set_curve_visible(
            mode is ControlMode.FAN_CURVES
            and self.current_profile is CurveProfile.CUSTOM
        )
        # Keep controls locked during the short visual handoff. Control logic is
        # already queued independently and is not delayed by this animation.
        self._set_manual_enabled(False)
        if mode is ControlMode.LENOVO_AUTO:
            self.fan1.set_target(None)
            self.fan2.set_target(None)
        self.telemetry_logger.event(
            f"Mode requested: {mode.value}",
            control_mode=mode.value,
            curve_profile=self.current_profile.value,
        )

    def _select_profile(self, profile: CurveProfile) -> None:
        self.current_profile = CurveProfile(profile)
        self.worker.request_profile(self.current_profile)
        self._set_curve_visible(self.current_profile is CurveProfile.CUSTOM)

    def _set_manual_enabled(self, enabled: bool) -> None:
        self.fan1.set_controls_enabled(enabled)
        self.fan2.set_controls_enabled(enabled)
        self.link_fans.setEnabled(enabled)

    def _manual_changed(self, fan: int, value: int) -> None:
        if self.link_fans.isChecked():
            other = self.fan2 if fan == 1 else self.fan1
            other.set_linked_target(value)
            fan1_target = fan2_target = value
        elif fan == 1:
            fan1_target = value
            fan2_target = self.fan2.effective_target()
        else:
            fan1_target = self.fan1.effective_target()
            fan2_target = value
        self.worker.request_manual(fan1_target, fan2_target)
        self.telemetry_logger.event(
            f"Manual targets requested: {fan1_target}/{fan2_target} RPM",
            control_mode=self.current_mode.value,
            fan1_target_rpm=fan1_target,
            fan2_target_rpm=fan2_target,
        )

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "fans_layout"):
            # The longer Lenovo mode names need more horizontal space than a
            # fan card.  Reflow telemetry earlier instead of squeezing text.
            self._apply_telemetry_layout(event.size().width() < 1050)
            self._apply_fan_layout(event.size().width() < 900)

    def _apply_telemetry_layout(self, compact: bool) -> None:
        if compact == self._compact_telemetry_layout:
            return
        self._compact_telemetry_layout = compact
        for card in (self.cpu_card, self.gpu_card, self.mode_card):
            self.telemetry_layout.removeWidget(card)
        if compact:
            self.telemetry_layout.addWidget(self.cpu_card, 0, 0)
            self.telemetry_layout.addWidget(self.gpu_card, 0, 1)
            self.telemetry_layout.addWidget(self.mode_card, 1, 0, 1, 2)
            self.telemetry_layout.setColumnStretch(0, 1)
            self.telemetry_layout.setColumnStretch(1, 1)
            self.telemetry_layout.setColumnStretch(2, 0)
        else:
            self.telemetry_layout.addWidget(self.cpu_card, 0, 0)
            self.telemetry_layout.addWidget(self.gpu_card, 0, 1)
            self.telemetry_layout.addWidget(self.mode_card, 0, 2)
            for column in range(3):
                self.telemetry_layout.setColumnStretch(column, 1)

    def _apply_fan_layout(self, compact: bool) -> None:
        if compact == self._compact_fan_layout:
            return
        self._compact_fan_layout = compact
        self.fans_layout.removeWidget(self.fan1)
        self.fans_layout.removeWidget(self.fan2)
        if compact:
            self.fans_layout.addWidget(self.fan1, 0, 0)
            self.fans_layout.addWidget(self.fan2, 1, 0)
            self.fans_layout.setColumnStretch(0, 1)
            self.fans_layout.setColumnStretch(1, 0)
        else:
            self.fans_layout.addWidget(self.fan1, 0, 0)
            self.fans_layout.addWidget(self.fan2, 0, 1)
            self.fans_layout.setColumnStretch(0, 1)
            self.fans_layout.setColumnStretch(1, 1)

    def _update_telemetry(self, sample) -> None:
        cpu_reading = getattr(sample, "cpu", None)
        gpu_reading = getattr(sample, "gpu", None)
        cpu_c = (
            cpu_reading.value_c
            if cpu_reading is not None and cpu_reading.valid
            else sample.cpu_c
            if cpu_reading is None and 0 < sample.cpu_c <= 125
            else None
        )
        gpu_c = (
            gpu_reading.value_c
            if gpu_reading is not None and gpu_reading.valid
            else sample.gpu_c
            if gpu_reading is None and 0 < sample.gpu_c <= 125
            else None
        )
        self.cpu_value.setText(f"{cpu_c} °C" if cpu_c is not None else "N/A")
        if gpu_reading is not None:
            gpu_text = f"{gpu_c} °C" if gpu_c is not None else "N/A"
            details = gpu_reading.reason or (
                f"Source: {gpu_reading.source}" if gpu_reading.valid else "GPU unavailable"
            )
            if gpu_reading.grace:
                details = f"{details} (bounded grace value)"
            gpu_tooltip = details
        else:
            gpu_text, gpu_tooltip = format_gpu_temperature(sample.gpu_c)
        self.gpu_value.setText(gpu_text)
        self.gpu_value.setToolTip(gpu_tooltip)
        self.lenovo_mode.setText(sample.lenovo_mode)
        self.mode_card.set_icon(
            str(resource_path(lenovo_mode_icon_asset(sample.fcmo_raw)))
        )
        self.fan1.set_actual(sample.fan1_rpm)
        self.fan2.set_actual(sample.fan2_rpm)
        if self.current_mode is ControlMode.LENOVO_AUTO:
            self.fan1.set_visual_rpm(sample.fan1_rpm)
            self.fan2.set_visual_rpm(sample.fan2_rpm)
            self.fan1.set_target(None)
            self.fan2.set_target(None)
        level, notifications = self.warning_tracker.update(cpu_c, gpu_c)
        if level is WarningLevel.NORMAL:
            if not self.thermal_protection.isChecked():
                self._set_warning(
                    "AUTOMATIC THERMAL PROTECTION DISABLED · custom fan control "
                    "will not switch to Lenovo Auto at a temperature limit"
                )
            elif self.worker.controller.thermal_latched:
                self._set_warning(
                    "THERMAL PROTECTION LATCHED · Lenovo Auto remains active"
                )
            else:
                self._set_warning(None)
            self.cpu_value.setStyleSheet("")
            self.gpu_value.setStyleSheet("")
        else:
            active = [
                component
                for component, current in self.warning_tracker.levels.items()
                if current >= WarningLevel.HIGH
            ]
            if level is WarningLevel.CRITICAL:
                text = (
                    "THERMAL PROTECTION · " + "/".join(active)
                    if self.thermal_protection.isChecked()
                    else "CRITICAL · "
                    + "/".join(active)
                    + " · automatic protection disabled"
                )
            else:
                text = "High " + "/".join(active) + " temperature"
            self._set_warning(text)
            self.cpu_value.setStyleSheet(
                "color:#ff3b55"
                if self.warning_tracker.levels["CPU"] >= WarningLevel.HIGH
                else ""
            )
            self.gpu_value.setStyleSheet(
                "color:#ff3b55"
                if self.warning_tracker.levels["GPU"] >= WarningLevel.HIGH
                else ""
            )
        for notification in notifications:
            show_temperature_notification(
                self.tray,
                notification,
                auto_restore_confirmed=(
                    self._protection_restore_confirmed
                    if notification.level is WarningLevel.CRITICAL
                    else None
                ),
                protection_enabled=self.thermal_protection.isChecked(),
            )
            self.log.warning(
                "Temperature warning: %s=%s °C",
                notification.component,
                notification.temperature_c,
            )
        self.telemetry_logger.log(
            {
                "cpu_c": "" if cpu_c is None else cpu_c,
                "gpu_c": "" if gpu_c is None else gpu_c,
                "gpu_source": (
                    gpu_reading.source if gpu_reading is not None else "EC"
                ),
                "gpu_valid": gpu_c is not None,
                "fcmo_raw": sample.fcmo_raw,
                "lenovo_mode": sample.lenovo_mode,
                "control_mode": self.current_mode.value,
                "curve_profile": self.current_profile.value,
                "fan1_actual_rpm": sample.fan1_rpm,
                "fan2_actual_rpm": sample.fan2_rpm,
                "fan1_target_rpm": self.fan1.committed_target or "",
                "fan2_target_rpm": self.fan2.committed_target or "",
                "ec_connected": self._connected,
            }
        )

    def _connection_changed(self, connected: bool, detail: str) -> None:
        self._connected = connected
        if connected:
            self.ec_status.setText("EC status: Connected")
            self.ec_status.setStyleSheet("color:#47df9b")
            self._update_live_status()
            if hasattr(self.worker.controller, "heartbeat"):
                self.safety_status.setText("Safety Service: Active")
                self.safety_status.setStyleSheet("color:#47df9b")
            else:
                self.safety_status.setText("Safety Service: Development direct mode")
                self.safety_status.setStyleSheet("color:#f2bb62")
            self.mode_buttons[ControlMode.MANUAL].setEnabled(True)
            self.mode_buttons[ControlMode.FAN_CURVES].setEnabled(True)
            for button in self.profile_buttons.values():
                button.setEnabled(True)
        else:
            self.ec_status.setText("EC status: Error")
            self.ec_status.setStyleSheet("color:#ff596d")
            self.message.setText(detail)
            self.fan1.set_actual_unavailable()
            self.fan2.set_actual_unavailable()
            self.safety_status.setText(
                "Safety Service: Error"
                if hasattr(self.worker.controller, "heartbeat")
                else "Safety Service: Development direct mode"
            )
            self.safety_status.setStyleSheet("color:#ff596d")
            self._set_manual_enabled(False)
            self.mode_buttons[ControlMode.MANUAL].setEnabled(False)
            self.mode_buttons[ControlMode.FAN_CURVES].setEnabled(False)
            for button in self.profile_buttons.values():
                button.setEnabled(False)
            self.telemetry_logger.event(
                f"EC/Safety Service connection error: {detail}",
                control_mode=self.current_mode.value,
                ec_connected=False,
            )

    def _mode_changed(self, mode_name: str) -> None:
        mode = ControlMode(mode_name)
        self.current_mode = mode
        self.fan1.set_control_mode(mode)
        self.fan2.set_control_mode(mode)
        self.mode_buttons[mode].setChecked(True)
        self._animate_mode_selection(mode)
        self.profile_panel.setVisible(mode is ControlMode.FAN_CURVES)
        self._set_curve_visible(
            mode is ControlMode.FAN_CURVES
            and self.current_profile is CurveProfile.CUSTOM
        )
        if self._connected and mode is ControlMode.MANUAL and not (
            self.fan1.is_slider_animating() or self.fan2.is_slider_animating()
        ):
            self._set_manual_enabled(True)
        else:
            self._set_manual_enabled(False)
        self.message.setText(f"Mode: {mode.value}")
        self.telemetry_logger.event(
            "Lenovo Auto Restore confirmed"
            if mode is ControlMode.LENOVO_AUTO
            else f"Mode active: {mode.value}",
            control_mode=mode.value,
            curve_profile=self.current_profile.value,
            ec_connected=self._connected,
        )

    def _targets_changed(self, targets: list[int | None]) -> None:
        self.fan1.set_target(targets[0])
        self.fan2.set_target(targets[1])
        if self.current_mode is ControlMode.FAN_CURVES:
            if targets[0] is not None:
                self.fan1.set_visual_rpm(targets[0])
            if targets[1] is not None:
                self.fan2.set_visual_rpm(targets[1])

    def _manual_initialized(self, targets: list[int]) -> None:
        self.fan1.set_visual_rpm(targets[0])
        self.fan2.set_visual_rpm(targets[1])
        QTimer.singleShot(300, self._enable_manual_after_animation)

    def _profile_changed(self, profile_name: str) -> None:
        self.current_profile = CurveProfile(profile_name)
        self.profile_buttons[self.current_profile].setChecked(True)
        self._set_curve_visible(
            self.current_mode is ControlMode.FAN_CURVES
            and self.current_profile is CurveProfile.CUSTOM
        )
        self.message.setText(f"Fan Curves · {self.current_profile.value}")
        self.telemetry_logger.event(
            "Profile changed",
            control_mode=self.current_mode.value,
            curve_profile=self.current_profile.value,
        )

    def _protection_triggered(self, reason: str, restored: bool) -> None:
        self._protection_restore_confirmed = bool(restored)
        if restored:
            text = f"THERMAL PROTECTION · {reason} · Lenovo Auto confirmed"
        else:
            text = f"CRITICAL · {reason} · Auto Restore FAILED"
        self._set_warning(text)
        self._set_manual_enabled(False)
        self.telemetry_logger.event(
            "Thermal protection Auto Restore confirmed"
            if restored
            else "Thermal protection Auto Restore failed",
            control_mode=ControlMode.LENOVO_AUTO.value,
        )

    def _enable_manual_after_animation(self) -> None:
        if self._connected and self.current_mode is ControlMode.MANUAL:
            self._set_manual_enabled(True)

    def _animate_mode_selection(self, mode: ControlMode) -> None:
        button = self.mode_buttons[mode]
        effect = self._mode_effects.get(mode)
        if effect is None:
            effect = QGraphicsOpacityEffect(button)
            button.setGraphicsEffect(effect)
            self._mode_effects[mode] = effect
        animation = self._mode_animations.get(mode)
        if animation is not None:
            animation.stop()
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(180)
        animation.setStartValue(0.68)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        self._mode_animations[mode] = animation
        animation.start()

    def _set_curve_visible(self, visible: bool, *, animated: bool = True) -> None:
        if visible == self._curve_visible and animated:
            return
        self._curve_visible = visible
        previous = getattr(self, "curve_transition", None)
        if previous is not None:
            previous.stop()
        target_height = max(390, self.curve_editor.sizeHint().height())
        if not animated:
            self.curve_editor.setVisible(visible)
            self.curve_editor.setMaximumHeight(target_height if visible else 0)
            self.curve_effect.setOpacity(1.0 if visible else 0.0)
            return
        if visible:
            self.curve_editor.show()
        height_animation = QPropertyAnimation(
            self.curve_editor, b"maximumHeight", self
        )
        height_animation.setDuration(270)
        height_animation.setStartValue(self.curve_editor.height() if visible else max(1, self.curve_editor.height()))
        height_animation.setEndValue(target_height if visible else 0)
        height_animation.setEasingCurve(QEasingCurve.OutCubic)
        fade_animation = QPropertyAnimation(self.curve_effect, b"opacity", self)
        fade_animation.setDuration(210)
        fade_animation.setStartValue(self.curve_effect.opacity())
        fade_animation.setEndValue(1.0 if visible else 0.0)
        fade_animation.setEasingCurve(QEasingCurve.OutCubic)
        group = QParallelAnimationGroup(self)
        group.addAnimation(height_animation)
        group.addAnimation(fade_animation)
        group.finished.connect(self._curve_transition_finished)
        self.curve_transition = group
        group.start()

    def _curve_transition_finished(self) -> None:
        if not self._curve_visible:
            self.curve_editor.hide()

    def _set_warning(self, text: str | None, *, animated: bool = True) -> None:
        visible = text is not None
        if visible:
            self.warning.setText(text or "")
        if visible == self._warning_visible:
            return
        self._warning_visible = visible
        previous = getattr(self, "warning_transition", None)
        if previous is not None:
            previous.stop()
        target_height = max(46, self.warning.sizeHint().height())
        if not animated:
            self.warning.setVisible(visible)
            self.warning.setMaximumHeight(target_height if visible else 0)
            self.warning_effect.setOpacity(1.0 if visible else 0.0)
            return
        if visible:
            self.warning.show()
        height_animation = QPropertyAnimation(self.warning, b"maximumHeight", self)
        height_animation.setDuration(220)
        height_animation.setStartValue(self.warning.height())
        height_animation.setEndValue(target_height if visible else 0)
        height_animation.setEasingCurve(QEasingCurve.OutCubic)
        fade_animation = QPropertyAnimation(self.warning_effect, b"opacity", self)
        fade_animation.setDuration(180)
        fade_animation.setStartValue(self.warning_effect.opacity())
        fade_animation.setEndValue(1.0 if visible else 0.0)
        group = QParallelAnimationGroup(self)
        group.addAnimation(height_animation)
        group.addAnimation(fade_animation)
        group.finished.connect(self._warning_transition_finished)
        self.warning_transition = group
        group.start()

    def _warning_transition_finished(self) -> None:
        if not self._warning_visible:
            self.warning.hide()

    def _show_error(self, error: str) -> None:
        self.message.setText(error)
        self.telemetry_logger.event(
            f"Operation error: {error}",
            control_mode=self.current_mode.value,
            ec_connected=self._connected,
        )

    def _start_logging(self) -> None:
        path = self.telemetry_logger.start()
        self.logging_status.setText(f"CSV logging · {path.name}")
        self.start_logging_button.setEnabled(False)
        self.stop_logging_button.setEnabled(True)
        self.export_log_button.setEnabled(False)
        self.telemetry_logger.event(
            "Logging started",
            control_mode=self.current_mode.value,
            curve_profile=self.current_profile.value,
        )
        self._update_live_status()

    def _stop_logging(self) -> None:
        if self.telemetry_logger.active:
            self.telemetry_logger.event("Logging stopped")
        self.telemetry_logger.stop()
        self.start_logging_button.setEnabled(True)
        self.stop_logging_button.setEnabled(False)
        self.export_log_button.setEnabled(
            self.telemetry_logger.path is not None
            and self.telemetry_logger.path.exists()
        )
        if self.telemetry_logger.last_error:
            self.logging_status.setText(
                f"Logging error: {self.telemetry_logger.last_error}"
            )
        else:
            self.logging_status.setText("CSV telemetry logging is off")
        self._update_live_status()

    def _update_live_status(self) -> None:
        if not self._connected:
            return
        if self.telemetry_logger.active:
            self.message.setText("Live sensor monitoring active · CSV logging on")
        else:
            self.message.setText(
                "Live sensor monitoring active · CSV logging off (not being saved)"
            )

    def _open_log_folder(self) -> None:
        directory = default_log_directory()
        directory.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory)))

    def _export_log(self) -> None:
        if self.telemetry_logger.path is None:
            return
        suggested = str(
            Path.home() / self.telemetry_logger.path.name
        )
        destination, _filter = QFileDialog.getSaveFileName(
            self, "Export telemetry CSV", suggested, "CSV files (*.csv)"
        )
        if not destination:
            return
        try:
            exported = self.telemetry_logger.export_to(Path(destination))
        except Exception as exc:
            self.logging_status.setText(f"Export failed: {exc}")
        else:
            self.logging_status.setText(f"Exported · {exported.name}")

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_from_tray()

    def prepare_suspend(self) -> None:
        if not self.worker.request_suspend(wait=True, timeout=3.0):
            self.log.error("Suspend Auto Restore did not complete before its deadline")

    def resume_from_suspend(self) -> None:
        self.worker.request_resume()

    def request_system_shutdown(self) -> None:
        """Explicit session-ending path; never route through tray-hide close."""
        self._session_ending = True
        self.exit_application()

    def exit_application(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._save_settings()
        self._stop_logging()
        self.worker.stop()
        if not self.worker.wait(7000):
            self.log.error("Worker did not stop before the shutdown deadline")
        self.tray.hide()
        QApplication.instance().quit()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closing or self._session_ending:
            event.accept()
            return
        if not self.run_in_background.isChecked():
            self.exit_application()
            event.accept()
            return
        self._save_settings()
        self.hide()
        if self.tray.isVisible():
            self.tray.showMessage(
                "YogaFan G8 is still running",
                "Telemetry and safety monitoring continue in the system tray.",
                QSystemTrayIcon.Information,
                3500,
            )
        event.ignore()
