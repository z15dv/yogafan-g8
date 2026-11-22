"""Polished telemetry card and aspect-ratio-safe icon presentation."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
)


class AspectRatioIcon(QLabel):
    """Render an asset centered in a square viewport without distortion."""

    def __init__(self, icon_path: str, viewport_size: int = 52) -> None:
        super().__init__()
        self._source = QPixmap()
        self._icon_path = ""
        self.setObjectName("aspectRatioIcon")
        self.setAlignment(Qt.AlignCenter)
        self.setScaledContents(False)
        self.setFixedSize(viewport_size, viewport_size)
        self.set_icon(icon_path)

    @property
    def icon_path(self) -> str:
        return self._icon_path

    @property
    def source_size(self) -> QSize:
        return self._source.size()

    def set_icon(self, icon_path: str) -> None:
        if icon_path == self._icon_path and not self._source.isNull():
            return
        source = QPixmap(icon_path)
        if source.isNull():
            self._source = QPixmap()
            self._icon_path = icon_path
            self.clear()
            return
        self._source = source
        self._icon_path = icon_path
        self._render_pixmap()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._render_pixmap()

    def _render_pixmap(self) -> None:
        if self._source.isNull() or self.width() <= 0 or self.height() <= 0:
            return
        # KeepAspectRatio guarantees that neither PNG nor SVG-backed pixmaps
        # can be stretched independently along one axis.  The viewport is
        # intentionally small, so large supplied PNGs are only downscaled.
        target = QSize(
            min(self.width(), self._source.width()),
            min(self.height(), self._source.height()),
        )
        self.setPixmap(
            self._source.scaled(
                target,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )


class TelemetryCard(QFrame):
    def __init__(self, caption: str, icon_path: str) -> None:
        super().__init__()
        self.setObjectName("telemetryCard")
        self.setAttribute(Qt.WA_Hover, True)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setFixedHeight(120)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(16)

        self.icon_tile = QFrame()
        self.icon_tile.setObjectName("telemetryIconTile")
        self.icon_tile.setFixedSize(72, 72)
        icon_layout = QVBoxLayout(self.icon_tile)
        icon_layout.setContentsMargins(10, 10, 10, 10)
        self.icon = AspectRatioIcon(icon_path, 52)
        icon_layout.addWidget(self.icon)
        layout.addWidget(self.icon_tile)

        text = QVBoxLayout()
        text.setSpacing(4)
        text.addStretch(1)
        self.caption = QLabel(caption)
        self.caption.setObjectName("telemetryCaption")
        self.caption.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        text.addWidget(self.caption)
        self.value = QLabel("—")
        self.value.setObjectName("telemetryValue")
        self.value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        text.addWidget(self.value)
        text.addStretch(1)
        layout.addLayout(text, 1)

    def set_value(self, value: str, tooltip: str = "") -> None:
        self.value.setText(value)
        self.value.setToolTip(tooltip)

    def set_icon(self, icon_path: str) -> None:
        self.icon.set_icon(icon_path)
