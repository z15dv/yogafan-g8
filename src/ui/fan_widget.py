"""Premium fan card with command-isolated animated presentation."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QElapsedTimer,
    QPropertyAnimation,
    QEvent,
    QRectF,
    QSize,
    QSignalBlocker,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)

from ec.protocol import (
    ControlMode,
    MAX_FAN_RPM,
    MIN_FAN_RPM,
    clamp_rpm,
    normalize_actual_rpm,
    normalize_target_rpm,
)


class IntentionalWheelSlider(QSlider):
    """Dark slider interaction that distinguishes handle drags from track clicks."""

    track_target_requested = Signal(int)

    def wheelEvent(self, event) -> None:
        if not self.hasFocus():
            event.ignore()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:
        if (
            event.button() != Qt.LeftButton
            or not self.isEnabled()
            or self.orientation() != Qt.Horizontal
        ):
            super().mousePressEvent(event)
            return

        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle = self.style().subControlRect(
            QStyle.CC_Slider,
            option,
            QStyle.SC_SliderHandle,
            self,
        )
        click = event.position().toPoint()
        if handle.contains(click):
            super().mousePressEvent(event)
            return

        groove = self.style().subControlRect(
            QStyle.CC_Slider,
            option,
            QStyle.SC_SliderGroove,
            self,
        )
        slider_min = groove.left()
        slider_max = max(slider_min, groove.right() - handle.width() + 1)
        position = round(event.position().x() - handle.width() / 2)
        target = QStyle.sliderValueFromPosition(
            self.minimum(),
            self.maximum(),
            max(0, min(slider_max - slider_min, position - slider_min)),
            max(1, slider_max - slider_min),
            option.upsideDown,
        )
        self.setFocus(Qt.MouseFocusReason)
        self.track_target_requested.emit(clamp_rpm(target))
        event.accept()


class FanVisual(QWidget):
    """Fan rendering driven exclusively by measured tachometer telemetry."""

    def __init__(self) -> None:
        super().__init__()
        self._actual_rpm: float | None = None
        self._display_rpm = 0.0
        self._rotation = 0.0
        self._target_marker_rpm: int | None = None
        self._last_telemetry_time: float | None = None
        self._telemetry_stale = True
        self._static_cache: QPixmap | None = None
        self._static_cache_key: tuple[int, int, float] | None = None
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setToolTip(
            "Rotor motion and cyan ring use measured Actual RPM only. "
            "The small marker indicates the requested Target RPM."
        )
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        self._clock = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance)

    def sizeHint(self) -> QSize:
        return QSize(124, 124)

    def minimumSizeHint(self) -> QSize:
        return QSize(100, 100)

    @property
    def actual_rpm(self) -> float | None:
        return self._actual_rpm

    @property
    def display_rpm(self) -> float:
        return self._display_rpm

    @property
    def rotation(self) -> float:
        return self._rotation

    @property
    def arc_progress(self) -> float:
        # The cyan ring and the white target marker share the manual-control
        # scale.  Keeping the old 0..MAX scale for the ring made equal actual
        # and target RPM land at visibly different angles.
        if self._display_rpm < 20:
            return 0.0
        return normalize_target_rpm(self._display_rpm)

    @property
    def speed_progress(self) -> float:
        return normalize_actual_rpm(self._display_rpm)

    @property
    def target_marker_progress(self) -> float | None:
        if self._target_marker_rpm is None:
            return None
        return normalize_target_rpm(self._target_marker_rpm)

    @property
    def telemetry_stale(self) -> bool:
        return self._telemetry_stale

    @staticmethod
    def visual_rps_for_rpm(rpm: float) -> float:
        normalized = normalize_actual_rpm(rpm)
        return 0.0 if rpm < 20 else 0.35 + 1.60 * (normalized ** 0.70)

    def set_actual_rpm(self, rpm: int | float) -> None:
        """Accept measured telemetry. No target-changing path may call this."""
        measured = max(0.0, float(rpm))
        first_sample = self._actual_rpm is None
        self._actual_rpm = measured
        self._last_telemetry_time = time.monotonic()
        self._telemetry_stale = False
        if first_sample:
            self._display_rpm = measured
        if self.isVisible() and not self._timer.isActive():
            self._clock.start()
            self._timer.start()
        self.update()

    def set_telemetry_stale(self) -> None:
        self._telemetry_stale = True
        self._timer.stop()
        self.update()

    def set_target_rpm(self, rpm: int | None) -> None:
        self._target_marker_rpm = None if rpm is None else clamp_rpm(rpm)
        self.update()

    # Compatibility name retained for older callers/tests. It is explicitly an
    # actual-telemetry input, not a requested-target input.
    def set_rpm(self, rpm: int, *, animated: bool = True) -> None:
        del animated
        self.set_actual_rpm(rpm)

    def _advance(self) -> None:
        if not self.isVisible() or self._telemetry_stale:
            self._timer.stop()
            return
        elapsed_seconds = min(0.05, max(0.0, self._clock.restart() / 1000.0))
        self.advance_for_test(elapsed_seconds)

    def advance_for_test(self, elapsed_seconds: float) -> None:
        """Advance one deterministic frame; useful for mock-only regression tests."""
        dt = min(0.05, max(0.0, float(elapsed_seconds)))
        target = self._actual_rpm if self._actual_rpm is not None else 0.0
        tau = 0.22 if target > self._display_rpm else 0.35
        alpha = 1.0 - math.exp(-dt / tau) if dt else 0.0
        self._display_rpm += (target - self._display_rpm) * alpha
        if target == 0 and self._display_rpm < 20:
            self._display_rpm = 0.0
        visual_rps = self.visual_rps_for_rpm(self._display_rpm)
        self._rotation = (
            self._rotation + 360.0 * visual_rps * dt
        ) % 360.0
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._telemetry_stale:
            self._clock.start()
            self._timer.start()

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:
        self._static_cache = None
        self._static_cache_key = None
        super().resizeEvent(event)

    def changeEvent(self, event) -> None:
        if event.type() in (
            QEvent.StyleChange,
            QEvent.PaletteChange,
            QEvent.DevicePixelRatioChange,
        ):
            self._static_cache = None
            self._static_cache_key = None
        super().changeEvent(event)

    def _housing_pixmap(self) -> QPixmap:
        ratio = self.devicePixelRatioF()
        key = (self.width(), self.height(), ratio)
        if self._static_cache is not None and self._static_cache_key == key:
            return self._static_cache
        width = max(1, round(self.width() * ratio))
        height = max(1, round(self.height() * ratio))
        pixmap = QPixmap(width, height)
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height()) - 6
        radius = side / 2
        painter.translate(self.width() / 2, self.height() / 2)
        outer = QRectF(-radius, -radius, side, side)
        shell = QRadialGradient(0, -radius * 0.15, radius)
        shell.setColorAt(0.0, QColor("#203246"))
        shell.setColorAt(0.72, QColor("#0d1722"))
        shell.setColorAt(1.0, QColor("#050a10"))
        painter.setBrush(shell)
        painter.setPen(QPen(QColor("#2d4962"), 2))
        painter.drawEllipse(outer)
        inner_radius = radius * 0.82
        painter.setBrush(QColor("#09111a"))
        painter.setPen(QPen(QColor("#132739"), 2))
        painter.drawEllipse(
            QRectF(-inner_radius, -inner_radius, inner_radius * 2, inner_radius * 2)
        )
        painter.end()
        self._static_cache = pixmap
        self._static_cache_key = key
        return pixmap

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.drawPixmap(0, 0, self._housing_pixmap())
        side = min(self.width(), self.height()) - 6
        center_x = self.width() / 2
        center_y = self.height() / 2
        radius = side / 2
        painter.translate(center_x, center_y)

        inner_radius = radius * 0.82
        normalized = self.arc_progress
        arc_radius = radius * 0.91
        arc_rect = QRectF(-arc_radius, -arc_radius, arc_radius * 2, arc_radius * 2)
        painter.setPen(QPen(QColor("#18384a"), 5.0, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(arc_rect, 225 * 16, 270 * 16)
        if normalized > 0:
            glow = QColor("#16cff7")
            glow.setAlpha(45 + round(normalized * 55))
            painter.setPen(QPen(glow, 9.0, Qt.SolidLine, Qt.RoundCap))
            painter.drawArc(arc_rect, 225 * 16, int(normalized * 270 * 16))
            arc_pen = QPen(QColor("#16cff7"), 5.0, Qt.SolidLine, Qt.RoundCap)
            painter.setPen(arc_pen)
            painter.drawArc(arc_rect, 225 * 16, int(normalized * 270 * 16))

        blade_gradient = QLinearGradient(0, -radius * 0.7, radius * 0.35, 0)
        blade_gradient.setColorAt(0.0, QColor("#465d76"))
        blade_gradient.setColorAt(0.55, QColor("#24374d"))
        blade_gradient.setColorAt(1.0, QColor("#111e2c"))
        blade = QPainterPath()
        blade.moveTo(-4, -radius * 0.12)
        blade.cubicTo(
            radius * 0.02,
            -radius * 0.45,
            radius * 0.15,
            -radius * 0.74,
            radius * 0.31,
            -radius * 0.68,
        )
        blade.cubicTo(
            radius * 0.45,
            -radius * 0.53,
            radius * 0.25,
            -radius * 0.22,
            radius * 0.08,
            -radius * 0.08,
        )
        blade.closeSubpath()

        motion = self.speed_progress
        disc = QRadialGradient(0, 0, inner_radius)
        disc.setColorAt(0.0, QColor(43, 69, 94, round(18 + motion * 36)))
        disc.setColorAt(0.72, QColor(25, 49, 69, round(14 + motion * 50)))
        disc.setColorAt(1.0, QColor(7, 16, 25, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(disc)
        painter.drawEllipse(
            QRectF(-inner_radius, -inner_radius, inner_radius * 2, inner_radius * 2)
        )

        def draw_blade_set(rotation: float, opacity: float) -> None:
            painter.save()
            painter.rotate(rotation)
            painter.setOpacity(opacity)
            painter.setBrush(blade_gradient)
            painter.setPen(QPen(QColor("#304860"), 0.8))
            for _ in range(6):
                painter.drawPath(blade)
                painter.rotate(60)
            painter.restore()

        blur_samples = round(motion * 5)
        blur_span = 4.0 + motion * 14.0
        blur_opacity = 0.10 + motion * 0.22
        for sample in range(blur_samples, 0, -1):
            offset = blur_span * sample / blur_samples
            draw_blade_set(
                self._rotation - offset,
                blur_opacity / blur_samples,
            )
        draw_blade_set(self._rotation, 0.92 - motion * 0.34)

        painter.setOpacity(1.0)
        hub_radius = radius * 0.20
        hub = QRadialGradient(-hub_radius * 0.3, -hub_radius * 0.4, hub_radius * 1.2)
        hub.setColorAt(0.0, QColor("#6f8daa"))
        hub.setColorAt(0.45, QColor("#314a64"))
        hub.setColorAt(1.0, QColor("#0a121c"))
        painter.setBrush(hub)
        painter.setPen(QPen(QColor("#5b7894"), 1.5))
        painter.drawEllipse(QRectF(-hub_radius, -hub_radius, hub_radius * 2, hub_radius * 2))

        if self._target_marker_rpm is not None:
            marker = self.target_marker_progress
            assert marker is not None
            angle = math.radians(225 + marker * 270)
            marker_radius = radius * 0.91
            x1 = math.cos(angle) * (marker_radius - 8)
            y1 = -math.sin(angle) * (marker_radius - 8)
            x2 = math.cos(angle) * (marker_radius + 1)
            y2 = -math.sin(angle) * (marker_radius + 1)
            painter.setPen(QPen(QColor("#d9f8ff"), 2.0, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(round(x1), round(y1), round(x2), round(y2))


class FanWidget(QFrame):
    target_changed = Signal(int)

    def __init__(self, title: str) -> None:
        super().__init__()
        self._visual_update = False
        self._dragging = False
        self._actual_rpm: int | None = None
        self._staged_target: int | None = None
        self._committed_target: int | None = None
        self._pending_commit: int | None = None
        self._control_mode = ControlMode.LENOVO_AUTO
        self.setObjectName("fanCard")
        self.setAttribute(Qt.WA_Hover, True)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setFixedHeight(225)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)

        header = QHBoxLayout()
        heading = QLabel(title)
        heading.setObjectName("fanTitle")
        self.target = QLabel("Target  —")
        self.target.setObjectName("fanTarget")
        header.addWidget(heading)
        header.addStretch(1)
        header.addWidget(self.target)
        layout.addLayout(header)

        body = QHBoxLayout()
        body.setSpacing(22)
        self.fan_visual = FanVisual()
        body.addWidget(self.fan_visual, 0, Qt.AlignVCenter)

        details = QVBoxLayout()
        details.setSpacing(8)
        details.addStretch(1)
        rpm_row = QHBoxLayout()
        rpm_row.setSpacing(8)
        self.actual = QLabel("—")
        self.actual.setObjectName("fanActualValue")
        self.actual.setMinimumWidth(88)
        self.actual.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.actual_unit = QLabel("RPM")
        self.actual_unit.setObjectName("fanActualUnit")
        rpm_row.addWidget(self.actual)
        rpm_row.addWidget(self.actual_unit, 0, Qt.AlignBottom)
        rpm_row.addStretch(1)
        details.addLayout(rpm_row)

        self.slider = IntentionalWheelSlider(Qt.Horizontal)
        self.slider.setFocusPolicy(Qt.StrongFocus)
        self.slider.setRange(MIN_FAN_RPM, MAX_FAN_RPM)
        self.slider.setSingleStep(100)
        self.slider.setPageStep(100)
        self.slider.setTickInterval(100)
        self.slider.setValue(3000)
        self.slider.valueChanged.connect(self._slider_value_changed)
        self.slider.sliderPressed.connect(self._begin_drag)
        self.slider.sliderReleased.connect(self._finish_drag)
        self.slider.track_target_requested.connect(self._track_target_requested)
        details.addWidget(self.slider)

        limits = QHBoxLayout()
        low = QLabel(f"{MIN_FAN_RPM} RPM")
        low.setObjectName("sliderLimit")
        high = QLabel("5300 RPM")
        high.setObjectName("sliderLimit")
        limits.addWidget(low)
        limits.addStretch(1)
        limits.addWidget(high)
        details.addLayout(limits)
        details.addStretch(1)
        body.addLayout(details, 1)
        layout.addLayout(body, 1)

        self.slider_animation = QPropertyAnimation(self.slider, b"value", self)
        self.slider_animation.setDuration(280)
        self.slider_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.slider_animation.finished.connect(self._visual_animation_finished)

    def _slider_value_changed(self, value: int) -> None:
        if self._visual_update:
            return
        snapped = clamp_rpm(value)
        if value != snapped:
            blocker = QSignalBlocker(self.slider)
            self.slider.setValue(snapped)
            del blocker
        if self._dragging:
            self._staged_target = snapped
        self.target.setText(f"Target  {snapped} RPM")

    def _begin_drag(self) -> None:
        self.slider_animation.stop()
        self._visual_update = False
        self._dragging = True
        self._staged_target = clamp_rpm(self.slider.value())
        self.target.setText(f"Target  {self._staged_target} RPM")

    def _finish_drag(self) -> None:
        if not self._dragging:
            return
        value = clamp_rpm(self.slider.value())
        blocker = QSignalBlocker(self.slider)
        self.slider.setValue(value)
        del blocker
        self._dragging = False
        self._staged_target = None
        self._committed_target = value
        self._pending_commit = value
        self.target.setText(f"Target  {value} RPM")
        self.fan_visual.set_target_rpm(value)
        self.target_changed.emit(value)

    def _track_target_requested(self, value: int) -> None:
        """Animate a groove click while producing exactly one logical commit."""
        if not self.slider.isEnabled():
            return
        value = clamp_rpm(value)
        self._dragging = False
        self._staged_target = None
        self._committed_target = value
        self._pending_commit = value
        self.target.setText(f"Target  {value} RPM")
        self.fan_visual.set_target_rpm(value)
        self.set_visual_rpm(value)
        self.target_changed.emit(value)

    def _visual_animation_finished(self) -> None:
        self._visual_update = False

    def set_visual_rpm(self, rpm: int, *, animated: bool = True) -> None:
        """Move the slider as presentation only; never emit a target command."""
        if self._dragging:
            return
        target = clamp_rpm(rpm)
        self.slider_animation.stop()
        if not animated:
            blocker = QSignalBlocker(self.slider)
            self.slider.setValue(target)
            del blocker
            self._visual_update = False
            return
        if self.slider.value() == target:
            self._visual_update = False
            return
        self._visual_update = True
        self.slider_animation.setStartValue(self.slider.value())
        self.slider_animation.setEndValue(target)
        self.slider_animation.start()

    def is_slider_animating(self) -> bool:
        return self.slider_animation.state() == QAbstractAnimation.Running

    def set_actual(self, rpm: int) -> None:
        self._actual_rpm = int(rpm)
        self.actual.setText(str(rpm))
        self.actual_unit.setVisible(True)
        self.fan_visual.set_actual_rpm(rpm)

    def set_actual_unavailable(self) -> None:
        self._actual_rpm = None
        self.actual.setText("—")
        self.actual_unit.setVisible(False)
        self.fan_visual.set_telemetry_stale()

    def set_target(self, rpm: int | None) -> None:
        if rpm is None:
            self._committed_target = None
            self._pending_commit = None
            if not self._dragging:
                self.target.setText("Target  —")
            self.fan_visual.set_target_rpm(None)
            return
        rpm = clamp_rpm(rpm)
        self._committed_target = rpm
        if self._pending_commit == rpm:
            self._pending_commit = None
        if self._dragging or self._pending_commit is not None:
            return
        self.target.setText(f"Target  {rpm} RPM")
        self.fan_visual.set_target_rpm(rpm)

    def set_linked_target(self, rpm: int) -> None:
        """Stage and animate a linked peer without emitting a second command."""
        if self._dragging:
            return
        rpm = clamp_rpm(rpm)
        self._committed_target = rpm
        self._pending_commit = rpm
        self.target.setText(f"Target  {rpm} RPM")
        self.fan_visual.set_target_rpm(rpm)
        self.set_visual_rpm(rpm)

    def set_control_mode(self, mode: ControlMode) -> None:
        self._control_mode = ControlMode(mode)
        if self._control_mode is ControlMode.LENOVO_AUTO:
            self.fan_visual.set_target_rpm(None)
        elif self._committed_target is not None:
            self.fan_visual.set_target_rpm(self._committed_target)

    def effective_target(self) -> int:
        return clamp_rpm(
            self._staged_target
            if self._staged_target is not None
            else self._pending_commit
            if self._pending_commit is not None
            else self._committed_target
            if self._committed_target is not None
            else self.slider.value()
        )

    @property
    def is_dragging(self) -> bool:
        return self._dragging

    @property
    def staged_target(self) -> int | None:
        return self._staged_target

    @property
    def committed_target(self) -> int | None:
        return self._committed_target

    def set_controls_enabled(self, enabled: bool) -> None:
        if not enabled and self._dragging:
            self._dragging = False
            self._staged_target = None
        self.slider.setEnabled(enabled)
