"""Thermal policy independent from UI presentation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from services.temperature_provider import TemperatureReading


class ThermalLevel(IntEnum):
    NORMAL = 0
    WARNING = 1
    CRITICAL = 2


@dataclass(frozen=True)
class ThermalStatus:
    cpu_level: ThermalLevel
    gpu_level: ThermalLevel
    critical_components: tuple[str, ...]
    warning_components: tuple[str, ...]

    @property
    def level(self) -> ThermalLevel:
        return max(self.cpu_level, self.gpu_level)

    @property
    def critical(self) -> bool:
        return bool(self.critical_components)


class ThermalProtectionPolicy:
    CPU_WARNING_C = 90
    CPU_CRITICAL_C = 95
    GPU_WARNING_C = 83
    GPU_CRITICAL_C = 85

    CPU_CRITICAL_MIN_C = 90
    CPU_CRITICAL_MAX_C = 100
    GPU_CRITICAL_MIN_C = 80
    GPU_CRITICAL_MAX_C = 90

    def __init__(
        self,
        *,
        enabled: bool = True,
        cpu_critical_c: int = CPU_CRITICAL_C,
        gpu_critical_c: int = GPU_CRITICAL_C,
    ) -> None:
        self.configure(enabled, cpu_critical_c, gpu_critical_c)

    def configure(
        self,
        enabled: bool,
        cpu_critical_c: int,
        gpu_critical_c: int,
    ) -> None:
        cpu = int(cpu_critical_c)
        gpu = int(gpu_critical_c)
        if not self.CPU_CRITICAL_MIN_C <= cpu <= self.CPU_CRITICAL_MAX_C:
            raise ValueError(
                f"CPU protection threshold must be {self.CPU_CRITICAL_MIN_C}–"
                f"{self.CPU_CRITICAL_MAX_C} °C"
            )
        if not self.GPU_CRITICAL_MIN_C <= gpu <= self.GPU_CRITICAL_MAX_C:
            raise ValueError(
                f"GPU protection threshold must be {self.GPU_CRITICAL_MIN_C}–"
                f"{self.GPU_CRITICAL_MAX_C} °C"
            )
        self.enabled = bool(enabled)
        self.cpu_critical_c = cpu
        self.gpu_critical_c = gpu
        self.cpu_warning_c = max(self.CPU_CRITICAL_MIN_C - 5, cpu - 5)
        self.gpu_warning_c = max(self.GPU_CRITICAL_MIN_C - 2, gpu - 2)

    @staticmethod
    def _level(reading: TemperatureReading, warning: int, critical: int) -> ThermalLevel:
        if not reading.valid or reading.value_c is None:
            return ThermalLevel.NORMAL
        if reading.value_c >= critical:
            return ThermalLevel.CRITICAL
        if reading.value_c >= warning:
            return ThermalLevel.WARNING
        return ThermalLevel.NORMAL

    def evaluate(
        self,
        cpu: TemperatureReading,
        gpu: TemperatureReading,
    ) -> ThermalStatus:
        cpu_level = self._level(cpu, self.cpu_warning_c, self.cpu_critical_c)
        gpu_level = self._level(gpu, self.gpu_warning_c, self.gpu_critical_c)
        warning = tuple(
            name
            for name, level in (("CPU", cpu_level), ("GPU", gpu_level))
            if level >= ThermalLevel.WARNING
        )
        critical = tuple(
            name
            for name, level in (("CPU", cpu_level), ("GPU", gpu_level))
            if level is ThermalLevel.CRITICAL
        )
        return ThermalStatus(cpu_level, gpu_level, critical, warning)
