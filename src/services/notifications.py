"""Component-aware thermal warning state and one-shot notifications."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from PySide6.QtWidgets import QSystemTrayIcon


class WarningLevel(IntEnum):
    NORMAL = 0
    HIGH = 1
    CRITICAL = 2


@dataclass(frozen=True)
class TemperatureWarning:
    component: str
    level: WarningLevel
    temperature_c: int


class TemperatureWarningTracker:
    THRESHOLDS = {"CPU": (90, 95), "GPU": (83, 85)}
    REARM_HYSTERESIS_C = {"CPU": 3, "GPU": 2}

    def __init__(
        self,
        *,
        cpu_critical_c: int = 95,
        gpu_critical_c: int = 85,
    ) -> None:
        self.levels = {"CPU": WarningLevel.NORMAL, "GPU": WarningLevel.NORMAL}
        self.configure(cpu_critical_c, gpu_critical_c)

    def configure(self, cpu_critical_c: int, gpu_critical_c: int) -> None:
        self.thresholds = {
            "CPU": (int(cpu_critical_c) - 5, int(cpu_critical_c)),
            "GPU": (int(gpu_critical_c) - 2, int(gpu_critical_c)),
        }
        self.levels = {"CPU": WarningLevel.NORMAL, "GPU": WarningLevel.NORMAL}
        self._notification_armed = {
            component: {
                WarningLevel.HIGH: True,
                WarningLevel.CRITICAL: True,
            }
            for component in self.thresholds
        }

    @property
    def level(self) -> WarningLevel:
        return max(self.levels.values())

    @staticmethod
    def _level(value: int | None, high: int, critical: int) -> WarningLevel:
        if value is None:
            return WarningLevel.NORMAL
        if value >= critical:
            return WarningLevel.CRITICAL
        if value >= high:
            return WarningLevel.HIGH
        return WarningLevel.NORMAL

    def update(
        self, cpu_c: int | None, gpu_c: int | None = None
    ) -> tuple[WarningLevel, list[TemperatureWarning]]:
        values = {"CPU": cpu_c, "GPU": gpu_c}
        notifications: list[TemperatureWarning] = []
        for component, value in values.items():
            high, critical = self.thresholds[component]
            current = self._level(value, high, critical)
            previous = self.levels[component]
            if value is not None:
                rearm_delta = self.REARM_HYSTERESIS_C[component]
                if value <= high - rearm_delta:
                    self._notification_armed[component][WarningLevel.HIGH] = True
                if value <= critical - rearm_delta:
                    self._notification_armed[component][WarningLevel.CRITICAL] = True
                if (
                    previous < WarningLevel.HIGH <= current
                    and self._notification_armed[component][WarningLevel.HIGH]
                ):
                    notifications.append(
                        TemperatureWarning(component, WarningLevel.HIGH, value)
                    )
                    self._notification_armed[component][WarningLevel.HIGH] = False
                if (
                    previous < WarningLevel.CRITICAL <= current
                    and self._notification_armed[component][WarningLevel.CRITICAL]
                ):
                    notifications.append(
                        TemperatureWarning(component, WarningLevel.CRITICAL, value)
                    )
                    self._notification_armed[component][WarningLevel.CRITICAL] = False
            self.levels[component] = current
        return self.level, notifications


def show_temperature_notification(
    tray_icon: QSystemTrayIcon,
    warning: TemperatureWarning,
    *,
    auto_restore_confirmed: bool | None = None,
    protection_enabled: bool = True,
) -> None:
    component = warning.component
    value = warning.temperature_c
    if warning.level is WarningLevel.CRITICAL:
        title = f"CRITICAL {component} TEMPERATURE"
        if not protection_enabled:
            action = "Automatic Lenovo Auto protection is disabled."
        elif auto_restore_confirmed is True:
            action = "Lenovo Auto restore was confirmed."
        elif auto_restore_confirmed is False:
            action = "Lenovo Auto restore FAILED; close workloads immediately."
        else:
            action = "Thermal protection is requesting Lenovo Auto."
        message = f"{component} reached {value} °C. {action}"
        icon = QSystemTrayIcon.Critical
    else:
        title = f"High {component} temperature"
        message = f"{component} reached {value} °C."
        icon = QSystemTrayIcon.Warning
    tray_icon.showMessage(title, message, icon, 8000)
