"""Staged, validated, independent CPU/GPU Custom curve editor."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from control.fan_curve import (
    BALANCED_CPU_CURVE,
    BALANCED_GPU_CURVE,
    CurveProfile,
    profile_curve_points,
    validate_points,
)
from ec.protocol import MAX_FAN_RPM, MIN_FAN_RPM, clamp_rpm


class NoWheelSpinBox(QSpinBox):
    """Curve values change via keyboard/buttons, never accidental scrolling."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class _CurveTable(QWidget):
    changed = Signal()

    def __init__(self, component: str, points: list[tuple[int, int]]) -> None:
        super().__init__()
        self.component = component
        self._building = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)
        helper = QLabel(
            f"{component} temperature is interpolated continuously between points."
        )
        helper.setObjectName("secondary")
        layout.addWidget(helper)
        self.table = QTableWidget(len(points), 2)
        self.table.setHorizontalHeaderLabels(
            [f"{component} temperature", "Fan target"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setMinimumHeight(285)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        self.table.setVerticalScrollMode(QTableWidget.ScrollPerPixel)
        layout.addWidget(self.table)
        self.set_points(points)

    def set_points(self, points) -> None:
        normalized = [(int(t), int(r)) for t, r in points]
        validate_points(normalized)
        self._building = True
        try:
            self.table.setRowCount(len(normalized))
            for row, (temperature, rpm) in enumerate(normalized):
                temp = NoWheelSpinBox()
                temp.setObjectName("curveInput")
                temp.setRange(30, 110)
                temp.setSuffix(" °C")
                temp.setKeyboardTracking(False)
                temp.setValue(temperature)
                fan = NoWheelSpinBox()
                fan.setObjectName("curveInput")
                fan.setRange(MIN_FAN_RPM, MAX_FAN_RPM)
                fan.setSingleStep(100)
                fan.setSuffix(" RPM")
                fan.setKeyboardTracking(False)
                fan.setValue(rpm)
                temp.valueChanged.connect(self._edited)
                fan.valueChanged.connect(self._edited)
                fan.editingFinished.connect(lambda row=row: self.snap_fan(row))
                self.table.setCellWidget(row, 0, temp)
                self.table.setCellWidget(row, 1, fan)
        finally:
            self._building = False

    def _edited(self) -> None:
        if not self._building:
            self.changed.emit()

    def points(self) -> list[tuple[int, int]]:
        return [
            (
                self.table.cellWidget(row, 0).value(),
                self.table.cellWidget(row, 1).value(),
            )
            for row in range(self.table.rowCount())
        ]

    def snap_fan(self, row: int) -> None:
        fan = self.table.cellWidget(row, 1)
        snapped = clamp_rpm(fan.value())
        if fan.value() != snapped:
            fan.setValue(snapped)
        self.changed.emit()

    @staticmethod
    def _set_invalid(widget, invalid: bool) -> None:
        widget.setProperty("invalid", invalid)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def validate_and_mark(self) -> bool:
        points = self.points()
        invalid_rows: set[int] = set()
        for row, (temperature, rpm) in enumerate(points):
            if row and temperature <= points[row - 1][0]:
                invalid_rows.update((row - 1, row))
            if rpm % 100 or not MIN_FAN_RPM <= rpm <= MAX_FAN_RPM:
                invalid_rows.add(row)
        try:
            validate_points(points)
        except ValueError:
            valid = False
        else:
            valid = not invalid_rows
        for row in range(self.table.rowCount()):
            for column in range(2):
                self._set_invalid(
                    self.table.cellWidget(row, column), row in invalid_rows
                )
        return valid


class CurveEditor(QFrame):
    """Custom profile editor; every edit remains staged until Apply."""

    apply_requested = Signal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self._building = False
        self._dirty = False
        self._applied_cpu: list[tuple[int, int]] = []
        self._applied_gpu: list[tuple[int, int]] = []
        self.setObjectName("card")
        self.setAttribute(Qt.WA_Hover, True)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        title = QLabel("CUSTOM FAN CURVE")
        title.setObjectName("cardTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Edit CPU and GPU demand independently. Hardware changes only after Apply Curve."
        )
        subtitle.setObjectName("secondary")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        copy_row = QHBoxLayout()
        copy_label = QLabel("Copy preset into Custom:")
        copy_label.setMinimumWidth(0)
        copy_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        copy_row.addWidget(copy_label)
        for profile in (
            CurveProfile.QUIET,
            CurveProfile.BALANCED,
            CurveProfile.PERFORMANCE,
        ):
            button = QPushButton(profile.value)
            button.setObjectName("compactButton")
            button.setMinimumWidth(0)
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            button.clicked.connect(
                lambda checked=False, p=profile: self.copy_profile(p)
            )
            copy_row.addWidget(button)
        copy_row.addStretch(1)
        layout.addLayout(copy_row)

        self.cpu_editor = _CurveTable("CPU", list(BALANCED_CPU_CURVE))
        self.gpu_editor = _CurveTable("GPU", list(BALANCED_GPU_CURVE))
        self.cpu_editor.changed.connect(self._on_edited)
        self.gpu_editor.changed.connect(self._on_edited)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("curveTabs")
        self.tabs.addTab(self.cpu_editor, "CPU Curve")
        self.tabs.addTab(self.gpu_editor, "GPU Curve")
        layout.addWidget(self.tabs)

        # V1-compatible aliases used by existing tests and integrations.
        self.table = self.cpu_editor.table

        footer = QHBoxLayout()
        self.status = QLabel("")
        self.status.setObjectName("secondary")
        footer.addWidget(self.status, 1)
        self.reset_button = QPushButton("Reset to Balanced")
        self.reset_button.setObjectName("secondaryButton")
        self.reset_button.clicked.connect(self._reset)
        footer.addWidget(self.reset_button)
        self.apply_button = QPushButton("Apply Curve")
        self.apply_button.setObjectName("primaryButton")
        self.apply_button.clicked.connect(self._apply)
        footer.addWidget(self.apply_button)
        layout.addLayout(footer)

        self.set_curves(BALANCED_CPU_CURVE, BALANCED_GPU_CURVE)

    def set_curves(self, cpu_points, gpu_points, *, mark_clean: bool = True) -> None:
        cpu = [(int(t), int(r)) for t, r in cpu_points]
        gpu = [(int(t), int(r)) for t, r in gpu_points]
        validate_points(cpu)
        validate_points(gpu)
        self._building = True
        try:
            self.cpu_editor.set_points(cpu)
            self.gpu_editor.set_points(gpu)
        finally:
            self._building = False
        self._dirty = not mark_clean
        if mark_clean:
            self._applied_cpu = list(cpu)
            self._applied_gpu = list(gpu)
        self.status.setText("" if mark_clean else "Changes staged — click Apply Curve")
        self._validate_editor()

    def set_points(self, points, *, mark_clean: bool = True) -> None:
        """V1 compatibility: replace only CPU points."""
        self.set_curves(points, self.gpu_points(), mark_clean=mark_clean)

    def points(self) -> list[tuple[int, int]]:
        return self.cpu_editor.points()

    def gpu_points(self) -> list[tuple[int, int]]:
        return self.gpu_editor.points()

    def applied_points(self) -> list[tuple[int, int]]:
        return list(self._applied_cpu)

    def applied_gpu_points(self) -> list[tuple[int, int]]:
        return list(self._applied_gpu)

    def _on_edited(self) -> None:
        if self._building:
            return
        self._dirty = True
        self.status.setText("Unsaved Custom curve changes")
        self._validate_editor()

    def _snap_fan(self, row: int) -> None:
        self.cpu_editor.snap_fan(row)

    def _validate_editor(self) -> bool:
        cpu_valid = self.cpu_editor.validate_and_mark()
        gpu_valid = self.gpu_editor.validate_and_mark()
        valid = cpu_valid and gpu_valid
        self.apply_button.setEnabled(self._dirty and valid)
        if not valid:
            bad = []
            if not cpu_valid:
                bad.append("CPU")
            if not gpu_valid:
                bad.append("GPU")
            self.status.setText(f"Fix highlighted {' and '.join(bad)} rows before applying")
            self.status.setProperty("invalid", True)
        else:
            self.status.setProperty("invalid", False)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        return valid

    def _apply(self) -> None:
        for editor in (self.cpu_editor, self.gpu_editor):
            for row in range(editor.table.rowCount()):
                editor.snap_fan(row)
        if not self._validate_editor():
            return
        cpu = self.points()
        gpu = self.gpu_points()
        self.apply_requested.emit(cpu, gpu)
        self._applied_cpu = list(cpu)
        self._applied_gpu = list(gpu)
        self._dirty = False
        self.apply_button.setEnabled(False)
        self.status.setText("Custom CPU/GPU curves applied")

    def copy_profile(self, profile: CurveProfile) -> None:
        cpu, gpu = profile_curve_points(profile)
        self.set_curves(cpu, gpu, mark_clean=False)
        self.status.setText(f"{profile.value} copied into Custom — click Apply Curve")

    def _reset(self) -> None:
        self.set_curves(BALANCED_CPU_CURVE, BALANCED_GPU_CURVE, mark_clean=False)
