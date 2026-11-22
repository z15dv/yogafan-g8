"""Local settings persistence through the Windows-native QSettings backend."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings

from control.fan_curve import (
    BALANCED_CPU_CURVE,
    BALANCED_GPU_CURVE,
    CurveProfile,
)
from control.safety import ThermalProtectionPolicy


class AppSettings:
    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings or QSettings("YogaFan G8", "YogaFan G8")

    def load(self) -> dict:
        legacy_curve = self._settings.value("curve", None)
        cpu_curve = self._settings.value("curves/custom_cpu", legacy_curve)
        gpu_curve = self._settings.value("curves/custom_gpu", None)
        if not isinstance(cpu_curve, list):
            cpu_curve = [list(point) for point in BALANCED_CPU_CURVE]
        if not isinstance(gpu_curve, list):
            gpu_curve = [list(point) for point in BALANCED_GPU_CURVE]
        raw_profile = self._settings.value(
            "curves/selected_profile", CurveProfile.BALANCED.value
        )
        try:
            profile = CurveProfile(str(raw_profile))
        except ValueError:
            profile = CurveProfile.BALANCED
        cpu_critical = self._settings.value(
            "safety/cpu_critical_c",
            ThermalProtectionPolicy.CPU_CRITICAL_C,
            type=int,
        )
        gpu_critical = self._settings.value(
            "safety/gpu_critical_c",
            ThermalProtectionPolicy.GPU_CRITICAL_C,
            type=int,
        )
        cpu_critical = max(
            ThermalProtectionPolicy.CPU_CRITICAL_MIN_C,
            min(ThermalProtectionPolicy.CPU_CRITICAL_MAX_C, cpu_critical),
        )
        gpu_critical = max(
            ThermalProtectionPolicy.GPU_CRITICAL_MIN_C,
            min(ThermalProtectionPolicy.GPU_CRITICAL_MAX_C, gpu_critical),
        )
        return {
            "geometry": self._settings.value("window/geometry", QByteArray()),
            "state": self._settings.value("window/state", QByteArray()),
            "link_fans": self._settings.value("fans/link", True, type=bool),
            "fan1_rpm": self._settings.value("fans/manual1", 3000, type=int),
            "fan2_rpm": self._settings.value("fans/manual2", 3000, type=int),
            "curve": cpu_curve,
            "custom_cpu_curve": cpu_curve,
            "custom_gpu_curve": gpu_curve,
            "curve_profile": profile,
            "nvml_fallback": self._settings.value(
                "sensors/nvml_fallback", True, type=bool
            ),
            "run_in_background": self._settings.value(
                "app/run_in_background", True, type=bool
            ),
            "temperature_notifications_enabled": self._settings.value(
                "notifications/temperature_enabled", True, type=bool
            ),
            "thermal_protection_enabled": self._settings.value(
                "safety/thermal_protection_enabled", True, type=bool
            ),
            "cpu_critical_c": cpu_critical,
            "gpu_critical_c": gpu_critical,
        }

    def save(self, values: dict) -> None:
        for key, value in values.items():
            self._settings.setValue(key, value)
        self._settings.sync()
