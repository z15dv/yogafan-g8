"""Independent CPU/GPU curves, presets, interpolation, and ramp-down hold."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Callable, Iterable

from ec.protocol import MAX_FAN_RPM, MIN_FAN_RPM, clamp_rpm

CurvePoints = tuple[tuple[int, int], ...]

QUIET_CPU_CURVE: CurvePoints = (
    (50, 1800), (60, 1800), (70, 2600), (80, 3900), (85, 4700), (90, 5300),
)
BALANCED_CPU_CURVE: CurvePoints = (
    (50, 1800), (60, 2200), (70, 3200), (80, 4200), (85, 4800), (90, 5300),
)
PERFORMANCE_CPU_CURVE: CurvePoints = (
    (50, 2400), (60, 3200), (70, 4000), (80, 4800), (85, 5300), (90, 5300),
)
QUIET_GPU_CURVE: CurvePoints = (
    (45, 1800), (55, 1900), (65, 2800), (75, 4200), (80, 5000), (83, 5300),
)
BALANCED_GPU_CURVE: CurvePoints = (
    (45, 1800), (55, 2200), (65, 3400), (75, 4500), (80, 5100), (83, 5300),
)
PERFORMANCE_GPU_CURVE: CurvePoints = (
    (45, 2400), (55, 3200), (65, 4200), (75, 5000), (80, 5300), (83, 5300),
)

# Backwards-compatible name used by YogaFan G8 v1 settings.
DEFAULT_CURVE: CurvePoints = BALANCED_CPU_CURVE


class CurveProfile(str, Enum):
    QUIET = "Quiet"
    BALANCED = "Balanced"
    PERFORMANCE = "Performance"
    CUSTOM = "Custom"


BUILTIN_PROFILE_CURVES: dict[CurveProfile, tuple[CurvePoints, CurvePoints]] = {
    CurveProfile.QUIET: (QUIET_CPU_CURVE, QUIET_GPU_CURVE),
    CurveProfile.BALANCED: (BALANCED_CPU_CURVE, BALANCED_GPU_CURVE),
    CurveProfile.PERFORMANCE: (PERFORMANCE_CPU_CURVE, PERFORMANCE_GPU_CURVE),
}


def validate_points(points: Iterable[tuple[int, int]]) -> None:
    normalized = [(int(temperature), int(rpm)) for temperature, rpm in points]
    if len(normalized) < 2:
        raise ValueError("A fan curve needs at least two points")
    previous_temp: int | None = None
    for temperature, rpm in normalized:
        if previous_temp is not None and temperature <= previous_temp:
            raise ValueError("Curve temperatures must be strictly increasing")
        if not MIN_FAN_RPM <= rpm <= MAX_FAN_RPM or rpm % 100:
            raise ValueError(
                f"Curve RPM values must be {MIN_FAN_RPM}..{MAX_FAN_RPM} "
                "in 100 RPM steps"
            )
        previous_temp = temperature


@dataclass
class FanCurve:
    points: list[tuple[int, int]] = field(default_factory=lambda: list(DEFAULT_CURVE))
    hysteresis_rpm: int = 75
    _last_output: int | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.points = [(int(t), int(r)) for t, r in self.points]
        validate_points(self.points)

    def interpolated_rpm(self, temperature_c: float) -> float:
        temperature_c = float(temperature_c)
        if temperature_c <= self.points[0][0]:
            return float(self.points[0][1])
        if temperature_c >= self.points[-1][0]:
            return float(self.points[-1][1])
        for (left_t, left_rpm), (right_t, right_rpm) in zip(
            self.points, self.points[1:]
        ):
            if left_t <= temperature_c <= right_t:
                fraction = (temperature_c - left_t) / (right_t - left_t)
                return left_rpm + fraction * (right_rpm - left_rpm)
        return float(self.points[-1][1])

    def requested_rpm(self, temperature_c: float) -> int:
        return clamp_rpm(self.interpolated_rpm(temperature_c))

    def target_rpm(self, temperature_c: float) -> int:
        """Apply small boundary hysteresis without adding a time delay."""
        continuous = max(
            MIN_FAN_RPM,
            min(MAX_FAN_RPM, self.interpolated_rpm(temperature_c)),
        )
        candidate = clamp_rpm(continuous)
        if self._last_output is None:
            self._last_output = candidate
        elif candidate > self._last_output:
            if continuous >= self._last_output + self.hysteresis_rpm:
                self._last_output = candidate
        elif candidate < self._last_output:
            if continuous <= self._last_output - self.hysteresis_rpm:
                self._last_output = candidate
        return self._last_output

    def reset_hysteresis(self) -> None:
        self._last_output = None

    def as_json(self) -> list[list[int]]:
        return [[temperature, rpm] for temperature, rpm in self.points]


@dataclass(frozen=True)
class CurveEvaluation:
    cpu_request_rpm: int
    gpu_request_rpm: int | None
    required_rpm: int
    effective_rpm: int
    ramp_down_pending: bool


class DualCurveController:
    """Combine two source curves and delay only reductions in fan demand."""

    def __init__(
        self,
        cpu_points: Iterable[tuple[int, int]],
        gpu_points: Iterable[tuple[int, int]],
        *,
        down_hold_seconds: float = 10.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cpu_curve = FanCurve(list(cpu_points))
        self.gpu_curve = FanCurve(list(gpu_points))
        self.down_hold_seconds = float(down_hold_seconds)
        self._monotonic = monotonic
        self._effective_rpm: int | None = None
        self._pending_lower_rpm: int | None = None
        self._pending_since: float | None = None

    @property
    def effective_rpm(self) -> int | None:
        return self._effective_rpm

    def evaluate(
        self,
        cpu_temperature_c: float,
        gpu_temperature_c: float | None,
        *,
        now: float | None = None,
        immediate: bool = False,
    ) -> CurveEvaluation:
        timestamp = self._monotonic() if now is None else float(now)
        cpu_request = self.cpu_curve.target_rpm(cpu_temperature_c)
        gpu_request = (
            self.gpu_curve.target_rpm(gpu_temperature_c)
            if gpu_temperature_c is not None
            else None
        )
        required = max(
            cpu_request,
            gpu_request if gpu_request is not None else MIN_FAN_RPM,
        )

        if self._effective_rpm is None or immediate or required > self._effective_rpm:
            self._effective_rpm = required
            self._clear_pending()
        elif required == self._effective_rpm:
            self._clear_pending()
        else:
            if self._pending_since is None:
                self._pending_since = timestamp
                self._pending_lower_rpm = required
            elif self._pending_lower_rpm is not None and required > self._pending_lower_rpm:
                # A warmer sample weakens the evidence for ramping down.
                self._pending_since = timestamp
                self._pending_lower_rpm = required
            else:
                # Continued or stronger reduction does not reset the hold timer.
                self._pending_lower_rpm = required
            if timestamp - self._pending_since >= self.down_hold_seconds:
                self._effective_rpm = required
                self._clear_pending()

        return CurveEvaluation(
            cpu_request,
            gpu_request,
            required,
            self._effective_rpm,
            self._pending_since is not None,
        )

    def reset(self) -> None:
        self.cpu_curve.reset_hysteresis()
        self.gpu_curve.reset_hysteresis()
        self._effective_rpm = None
        self._clear_pending()

    def _clear_pending(self) -> None:
        self._pending_lower_rpm = None
        self._pending_since = None


def profile_curve_points(
    profile: CurveProfile,
    custom_cpu: Iterable[tuple[int, int]] | None = None,
    custom_gpu: Iterable[tuple[int, int]] | None = None,
) -> tuple[CurvePoints, CurvePoints]:
    profile = CurveProfile(profile)
    if profile in BUILTIN_PROFILE_CURVES:
        return BUILTIN_PROFILE_CURVES[profile]
    cpu = tuple((int(t), int(r)) for t, r in (custom_cpu or BALANCED_CPU_CURVE))
    gpu = tuple((int(t), int(r)) for t, r in (custom_gpu or BALANCED_GPU_CURVE))
    validate_points(cpu)
    validate_points(gpu)
    return cpu, gpu
