"""Resolved telemetry passed from the hardware worker to UI and logging."""

from __future__ import annotations

from dataclasses import dataclass

from ec.lenovo_gen8_ec import Telemetry
from .temperature_provider import TemperatureReading


@dataclass(frozen=True)
class TelemetrySnapshot:
    raw: Telemetry
    cpu: TemperatureReading
    gpu: TemperatureReading

    @property
    def cpu_c(self) -> int:
        # The EC telemetry object retains its raw value for diagnostics. Control
        # code must consult ``cpu.valid`` before using it.
        return self.raw.cpu_c

    @property
    def gpu_c(self) -> int:
        return self.gpu.value_c or 0

    @property
    def fcmo_raw(self) -> int:
        return self.raw.fcmo_raw

    @property
    def fan1_rpm(self) -> int:
        return self.raw.fan1_rpm

    @property
    def fan2_rpm(self) -> int:
        return self.raw.fan2_rpm

    @property
    def lenovo_mode(self) -> str:
        return self.raw.lenovo_mode
