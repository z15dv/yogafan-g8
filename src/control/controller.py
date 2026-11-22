"""Thread-safe fan control and explicit safety-state transitions."""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum
import logging
import threading

from ec.backend import EcBackend
from ec.lenovo_gen8_ec import LenovoGen8Ec, Telemetry
from ec.protocol import ControlMode, clamp_rpm, rpm_to_raw
from .fan_curve import (
    BALANCED_CPU_CURVE,
    BALANCED_GPU_CURVE,
    CurveEvaluation,
    CurveProfile,
    DualCurveController,
    FanCurve,
    profile_curve_points,
)


class CommunicationBlockedError(RuntimeError):
    """Writes are paused until a successful telemetry read proves recovery."""


class ControlStateError(RuntimeError):
    """A requested transition is unsafe in the controller's current state."""


class ApplicationState(str, Enum):
    STARTING = "STARTING"
    LENOVO_AUTO = "LENOVO_AUTO"
    MANUAL_ACTIVE = "MANUAL_ACTIVE"
    CURVE_ACTIVE = "CURVE_ACTIVE"
    THERMAL_PROTECTION = "THERMAL_PROTECTION"
    EC_ERROR = "EC_ERROR"
    SERVICE_DISCONNECTED = "SERVICE_DISCONNECTED"
    SHUTTING_DOWN = "SHUTTING_DOWN"


class FanController:
    def __init__(
        self,
        backend_factory: Callable[[], EcBackend],
        curve: FanCurve | None = None,
        ec_timeout: float = 0.5,
        *,
        control_authorized: Callable[[], bool] | None = None,
    ) -> None:
        self._backend_factory = backend_factory
        self._lock = threading.RLock()
        initial_cpu = tuple((curve or FanCurve()).points)
        self._custom_cpu = tuple(initial_cpu)
        self._custom_gpu = tuple(BALANCED_GPU_CURVE)
        self.profile = CurveProfile.BALANCED
        self._curve_engine = DualCurveController(
            BALANCED_CPU_CURVE,
            BALANCED_GPU_CURVE,
        )
        self._ec_timeout = ec_timeout
        self._control_authorized = control_authorized or (lambda: True)
        self.mode = ControlMode.LENOVO_AUTO
        self.state = ApplicationState.STARTING
        self.target_rpm: list[int | None] = [None, None]
        self.writes_blocked = False
        self.last_error: str | None = None
        self._shutdown = False
        self._thermal_latched = False
        self._command_generation = 0
        self.log = logging.getLogger("yogafan.controller")

    @property
    def curve(self) -> FanCurve:
        """Compatibility alias for the active CPU curve."""
        return self._curve_engine.cpu_curve

    @property
    def curve_engine(self) -> DualCurveController:
        return self._curve_engine

    @property
    def custom_curves(self) -> tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]:
        return self._custom_cpu, self._custom_gpu

    @property
    def command_generation(self) -> int:
        """Changes whenever queued custom-control commands must be discarded."""
        return self._command_generation

    @property
    def thermal_latched(self) -> bool:
        return self._thermal_latched

    def set_curve(self, curve: FanCurve) -> None:
        """Compatibility helper: update Custom CPU while retaining Custom GPU."""
        self.set_custom_curves(curve.points, self._custom_gpu)

    def set_custom_curves(
        self,
        cpu_points: list[tuple[int, int]] | tuple[tuple[int, int], ...],
        gpu_points: list[tuple[int, int]] | tuple[tuple[int, int], ...],
    ) -> None:
        # Construction validates both sets before changing any live state.
        candidate = DualCurveController(cpu_points, gpu_points)
        self._custom_cpu = tuple(candidate.cpu_curve.points)
        self._custom_gpu = tuple(candidate.gpu_curve.points)
        if self.profile is CurveProfile.CUSTOM:
            self._curve_engine = candidate
            self.target_rpm = [None, None]

    def set_profile(self, profile: CurveProfile) -> None:
        self._require_custom_control_allowed()
        profile = CurveProfile(profile)
        cpu_points, gpu_points = profile_curve_points(
            profile, self._custom_cpu, self._custom_gpu
        )
        self.profile = profile
        self._curve_engine = DualCurveController(cpu_points, gpu_points)
        self.target_rpm = [None, None]
        self.log.info("Curve profile changed to %s", profile.value)

    def _run_session(self, operation):
        with self._lock:
            backend = self._backend_factory()
            try:
                backend.open()
                ec = LenovoGen8Ec(
                    backend, lock=self._lock, timeout=self._ec_timeout
                )
                return operation(ec)
            finally:
                backend.close()

    def initialize(self) -> None:
        """Every process start explicitly hands fan control to Lenovo."""
        self.log.info("Initializing in Lenovo Auto mode")
        self.restore_auto(reason="startup")

    def read_telemetry(self) -> Telemetry:
        recovering_from_error = self.state is ApplicationState.EC_ERROR
        try:
            telemetry = self._run_session(lambda ec: ec.read_telemetry())
        except Exception as exc:
            self.last_error = str(exc)
            self.writes_blocked = True
            self.state = ApplicationState.EC_ERROR
            self._invalidate_custom_commands()
            self.log.error("EC telemetry failed: %s", exc)
            raise
        self.last_error = None
        self.writes_blocked = False
        if recovering_from_error:
            # A healthy read proves communication is back, but the EC may have
            # retained an earlier manual request. Re-establish Auto before any
            # new custom command is allowed.
            self.restore_auto(reason="EC communication recovery")
        return telemetry

    def restore_auto(self, *, reason: str = "user", preserve_state: bool = False) -> None:
        try:
            self._run_session(lambda ec: ec.restore_auto())
        except Exception as exc:
            self.last_error = str(exc)
            self.writes_blocked = True
            if self.state is not ApplicationState.SHUTTING_DOWN:
                self.state = ApplicationState.EC_ERROR
            self.log.error("Auto Restore failed: %s", exc)
            raise
        self.mode = ControlMode.LENOVO_AUTO
        self.target_rpm = [None, None]
        self.last_error = None
        self.writes_blocked = False
        self._curve_engine.reset()
        self._invalidate_custom_commands()
        if not preserve_state:
            self.state = (
                ApplicationState.THERMAL_PROTECTION
                if self._thermal_latched
                else ApplicationState.LENOVO_AUTO
            )
        self.log.info("Lenovo Auto Restore confirmed with 0xAC (%s)", reason)

    def _invalidate_custom_commands(self) -> None:
        self._command_generation += 1

    def _require_custom_control_allowed(self) -> None:
        if self.state is ApplicationState.STARTING:
            raise ControlStateError(
                "Lenovo Auto must be confirmed before custom fan control"
            )
        if self._shutdown or self.state is ApplicationState.SHUTTING_DOWN:
            raise ControlStateError("Fan control is shutting down")
        if self._thermal_latched:
            raise ControlStateError(
                "Thermal protection is latched; remain in Lenovo Auto"
            )
        if not self._control_authorized():
            self.state = ApplicationState.SERVICE_DISCONNECTED
            raise ControlStateError("Safety Service is not connected")
        if self.writes_blocked or self.state is ApplicationState.EC_ERROR:
            raise CommunicationBlockedError(
                "Fan writes are paused until Lenovo Auto is confirmed again"
            )

    def set_mode(self, mode: ControlMode) -> None:
        mode = ControlMode(mode)
        if mode is ControlMode.LENOVO_AUTO:
            self.restore_auto()
            return
        self._require_custom_control_allowed()
        self.mode = mode
        self.target_rpm = [None, None]
        if mode is ControlMode.FAN_CURVES:
            self._curve_engine.reset()
            self.state = ApplicationState.CURVE_ACTIVE
        else:
            self.state = ApplicationState.MANUAL_ACTIVE
        self.log.info("Control mode changed to %s", mode.value)

    def set_manual_targets(self, fan1_rpm: int, fan2_rpm: int) -> None:
        if self.mode is not ControlMode.MANUAL:
            raise RuntimeError("Manual targets require Manual mode")
        self._require_custom_control_allowed()
        self._set_targets(fan1_rpm, fan2_rpm)

    def enter_manual_from_actual(
        self, fan1_actual_rpm: int, fan2_actual_rpm: int, linked: bool
    ) -> tuple[int, int]:
        """Enter Manual mode by picking up from the currently measured state."""
        if linked:
            shared = clamp_rpm((fan1_actual_rpm + fan2_actual_rpm) / 2)
            fan1_target = fan2_target = shared
        else:
            fan1_target = clamp_rpm(fan1_actual_rpm)
            fan2_target = clamp_rpm(fan2_actual_rpm)
        self.set_mode(ControlMode.MANUAL)
        self._set_targets(fan1_target, fan2_target)
        return fan1_target, fan2_target

    def _set_targets(self, fan1_rpm: int, fan2_rpm: int) -> None:
        self._require_custom_control_allowed()
        raw1, raw2 = rpm_to_raw(fan1_rpm), rpm_to_raw(fan2_rpm)
        changed1 = self.target_rpm[0] != fan1_rpm
        changed2 = self.target_rpm[1] != fan2_rpm
        if not changed1 and not changed2:
            return

        def write_changed(ec: LenovoGen8Ec) -> None:
            if changed1:
                ec.set_fan(1, raw1)
            if changed2:
                ec.set_fan(2, raw2)

        try:
            self._run_session(write_changed)
        except Exception as exc:
            self.last_error = str(exc)
            self.writes_blocked = True
            self.state = ApplicationState.EC_ERROR
            self._invalidate_custom_commands()
            self.log.error("Fan command failed: %s", exc)
            raise
        self.target_rpm = [fan1_rpm, fan2_rpm]
        self.last_error = None
        self.log.info("Fan targets changed: Fan 1=%s, Fan 2=%s RPM", fan1_rpm, fan2_rpm)

    def apply_curves(
        self,
        cpu_temperature: float,
        gpu_temperature: float | None,
        *,
        now: float | None = None,
        immediate: bool = False,
    ) -> CurveEvaluation | None:
        if self.mode is not ControlMode.FAN_CURVES:
            return None
        self._require_custom_control_allowed()
        evaluation = self._curve_engine.evaluate(
            cpu_temperature,
            gpu_temperature,
            now=now,
            immediate=immediate,
        )
        self._set_targets(evaluation.effective_rpm, evaluation.effective_rpm)
        return evaluation

    def apply_curve(self, cpu_temperature: int) -> int | None:
        """Compatibility wrapper for callers without a GPU reading."""
        evaluation = self.apply_curves(cpu_temperature, None)
        return evaluation.effective_rpm if evaluation else None

    def poll(self) -> Telemetry:
        return self.read_telemetry()

    def trigger_thermal_protection(self, reason: str) -> bool:
        """Latch protection and synchronously require a confirmed Auto restore."""
        if self._thermal_latched and self.mode is ControlMode.LENOVO_AUTO:
            return True
        self._thermal_latched = True
        self.state = ApplicationState.THERMAL_PROTECTION
        self._invalidate_custom_commands()
        try:
            self.restore_auto(reason=reason, preserve_state=True)
        except Exception:
            # The latch remains set and no custom writes can follow a failed restore.
            return False
        self.state = ApplicationState.THERMAL_PROTECTION
        return True

    def clear_thermal_latch(self) -> None:
        """Explicit acknowledgement; never reactivates the previous mode."""
        if self.mode is not ControlMode.LENOVO_AUTO:
            raise ControlStateError("Thermal latch can only clear in Lenovo Auto")
        self._thermal_latched = False
        self.state = ApplicationState.LENOVO_AUTO

    def suspend(self) -> bool:
        """Best effort restore; the transient session is closed before return."""
        try:
            self.restore_auto(reason="suspend")
            return True
        except Exception:
            self.log.exception("Suspend Auto Restore failed")
            return False

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self.state = ApplicationState.SHUTTING_DOWN
        self._invalidate_custom_commands()
        try:
            self.restore_auto(reason="shutdown", preserve_state=True)
        finally:
            self._shutdown = True
            self.state = ApplicationState.SHUTTING_DOWN
            self.log.info("Controller shutdown complete")
