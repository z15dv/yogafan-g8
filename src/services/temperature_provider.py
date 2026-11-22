"""Temperature sensor models and a power-conscious NVIDIA fallback."""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import time
from typing import Callable, Protocol


EC_MIN_VALID_C = 1
EC_MAX_VALID_C = 125


@dataclass(frozen=True)
class TemperatureReading:
    value_c: int | None
    source: str
    timestamp: float
    valid: bool
    sleeping: bool = False
    reason: str = ""
    grace: bool = False

    def is_fresh(self, now: float, max_age: float) -> bool:
        return self.valid and now - self.timestamp <= max_age

    @classmethod
    def unavailable(
        cls,
        timestamp: float,
        reason: str,
        *,
        sleeping: bool = False,
    ) -> "TemperatureReading":
        return cls(None, "Unavailable", timestamp, False, sleeping, reason)


class TemperatureProvider(Protocol):
    def read(self, now: float | None = None) -> TemperatureReading: ...

    def close(self) -> None: ...


class NvmlGpuTemperatureProvider:
    """Lazy NVML reader; importing or initializing NVML happens only on demand."""

    def __init__(
        self,
        module_loader: Callable[[], object] | None = None,
        device_index: int = 0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._module_loader = module_loader or (
            lambda: importlib.import_module("pynvml")
        )
        self._device_index = device_index
        self._monotonic = monotonic
        self._module = None
        self._handle = None
        self._initialized = False

    def _initialize(self) -> None:
        if self._initialized:
            return
        module = self._module_loader()
        module.nvmlInit()
        self._handle = module.nvmlDeviceGetHandleByIndex(self._device_index)
        self._module = module
        self._initialized = True

    def read(self, now: float | None = None) -> TemperatureReading:
        timestamp = self._monotonic() if now is None else float(now)
        try:
            self._initialize()
            value = int(
                self._module.nvmlDeviceGetTemperature(
                    self._handle,
                    self._module.NVML_TEMPERATURE_GPU,
                )
            )
            if not EC_MIN_VALID_C <= value <= EC_MAX_VALID_C:
                return TemperatureReading.unavailable(
                    timestamp, f"NVML returned implausible temperature {value}"
                )
            return TemperatureReading(value, "NVML", timestamp, True)
        except Exception as exc:
            # NVML does not expose a reliable, portable distinction between an
            # Optimus device sleeping and every driver-level failure. Do not
            # guess: keep the reason informational and the value unavailable.
            return TemperatureReading.unavailable(
                timestamp, f"NVML unavailable: {exc}"
            )

    def close(self) -> None:
        if not self._initialized or self._module is None:
            return
        try:
            self._module.nvmlShutdown()
        except Exception:
            pass
        finally:
            self._initialized = False
            self._handle = None
            self._module = None


class GpuTemperatureProvider:
    """Prefer valid EC data and throttle the optional NVML fallback."""

    def __init__(
        self,
        nvml: TemperatureProvider | None = None,
        *,
        fallback_enabled: bool = True,
        fallback_interval: float = 10.0,
        fallback_max_age: float = 12.0,
        grace_period: float = 3.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.nvml = nvml or NvmlGpuTemperatureProvider(monotonic=monotonic)
        self.fallback_enabled = fallback_enabled
        self.fallback_interval = float(fallback_interval)
        self.fallback_max_age = float(fallback_max_age)
        self.grace_period = float(grace_period)
        self._monotonic = monotonic
        self._last_fallback_attempt: float | None = None
        self._last_fallback: TemperatureReading | None = None
        self._last_valid: TemperatureReading | None = None

    @staticmethod
    def is_valid_ec(value_c: int | None) -> bool:
        return value_c is not None and EC_MIN_VALID_C <= int(value_c) <= EC_MAX_VALID_C

    def resolve(
        self, ec_value_c: int | None, now: float | None = None
    ) -> TemperatureReading:
        timestamp = self._monotonic() if now is None else float(now)
        if self.is_valid_ec(ec_value_c):
            reading = TemperatureReading(int(ec_value_c), "EC", timestamp, True)
            self._last_valid = reading
            return reading

        fallback: TemperatureReading | None = None
        if self.fallback_enabled:
            should_query = (
                self._last_fallback_attempt is None
                or timestamp - self._last_fallback_attempt >= self.fallback_interval
            )
            if should_query:
                self._last_fallback_attempt = timestamp
                self._last_fallback = self.nvml.read(timestamp)
            fallback = self._last_fallback
            if fallback is not None and fallback.is_fresh(
                timestamp, self.fallback_max_age
            ):
                self._last_valid = fallback
                return fallback

        if (
            self._last_valid is not None
            and timestamp - self._last_valid.timestamp <= self.grace_period
        ):
            last = self._last_valid
            return TemperatureReading(
                last.value_c,
                last.source,
                last.timestamp,
                True,
                last.sleeping,
                "Short bounded grace after sensor loss",
                True,
            )

        if fallback is not None and not fallback.valid:
            return TemperatureReading.unavailable(
                timestamp,
                fallback.reason or "GPU sensors unavailable",
                sleeping=fallback.sleeping,
            )
        reason = (
            "EC GPU sensor unavailable; NVML fallback disabled"
            if not self.fallback_enabled
            else "EC and NVML GPU sensors unavailable"
        )
        return TemperatureReading.unavailable(timestamp, reason)

    def close(self) -> None:
        self.nvml.close()


def cpu_temperature_reading(
    value_c: int | None, now: float | None = None
) -> TemperatureReading:
    timestamp = time.monotonic() if now is None else float(now)
    if value_c is not None and EC_MIN_VALID_C <= int(value_c) <= EC_MAX_VALID_C:
        return TemperatureReading(int(value_c), "EC", timestamp, True)
    return TemperatureReading.unavailable(timestamp, "CPU EC sensor unavailable")
