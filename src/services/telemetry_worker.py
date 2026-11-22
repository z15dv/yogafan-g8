"""QThread owning every EC transaction and the polling schedule."""

from __future__ import annotations

import logging
import queue
import threading
import time

from PySide6.QtCore import QThread, Signal

from control.controller import ApplicationState, FanController
from control.fan_curve import CurveProfile
from control.safety import ThermalProtectionPolicy
from ec.lenovo_gen8_ec import Telemetry
from ec.protocol import ControlMode
from .temperature_provider import (
    GpuTemperatureProvider,
    TemperatureProvider,
    cpu_temperature_reading,
)
from .telemetry import TelemetrySnapshot


class TelemetryWorker(QThread):
    telemetry = Signal(object)
    connection_changed = Signal(bool, str)
    mode_changed = Signal(str)
    targets_changed = Signal(object)
    manual_initialized = Signal(object)
    profile_changed = Signal(str)
    thermal_status = Signal(object)
    protection_triggered = Signal(str, bool)
    operation_error = Signal(str)

    def __init__(
        self,
        controller: FanController,
        interval: float = 0.85,
        *,
        gpu_provider: TemperatureProvider | None = None,
        nvml_fallback_enabled: bool = True,
    ) -> None:
        super().__init__()
        self.controller = controller
        self.interval = interval
        self.gpu_provider = gpu_provider or GpuTemperatureProvider(
            fallback_enabled=nvml_fallback_enabled
        )
        self.thermal_policy = ThermalProtectionPolicy()
        self._commands: queue.Queue[tuple] = queue.Queue()
        self._stop_event = threading.Event()
        self._suspended = False
        self._last_protection_attempt: float | None = None
        self.log = logging.getLogger("yogafan.worker")

    def request_mode(self, mode: ControlMode, link_fans: bool = False) -> None:
        self._commands.put(
            (
                "mode",
                self.controller.command_generation,
                ControlMode(mode),
                bool(link_fans),
            )
        )

    def request_manual(self, fan1_rpm: int, fan2_rpm: int) -> None:
        self._commands.put(
            (
                "manual",
                self.controller.command_generation,
                int(fan1_rpm),
                int(fan2_rpm),
            )
        )

    def request_curve(
        self,
        cpu_points: list[tuple[int, int]],
        gpu_points: list[tuple[int, int]] | None = None,
    ) -> None:
        # The optional second argument keeps the v1 API safe during migration.
        gpu_points = gpu_points or list(self.controller.custom_curves[1])
        self._commands.put(("curves", cpu_points, gpu_points, True))

    def configure_custom_curves(
        self,
        cpu_points: list[tuple[int, int]],
        gpu_points: list[tuple[int, int]],
    ) -> None:
        """Load persisted Custom data without activating hardware control."""
        self._commands.put(("curves", cpu_points, gpu_points, False))

    def request_profile(self, profile: CurveProfile) -> None:
        self._commands.put(
            (
                "profile",
                self.controller.command_generation,
                CurveProfile(profile),
            )
        )

    def configure_profile(self, profile: CurveProfile) -> None:
        """Load a persisted selection without activating Fan Curves mode."""
        self._commands.put(("profile_data", CurveProfile(profile)))

    def request_suspend(self, *, wait: bool = False, timeout: float = 3.0) -> bool:
        completed = threading.Event() if wait else None
        result = {"success": False}
        self._commands.put(("suspend", completed, result))
        if completed is None:
            return True
        return completed.wait(timeout) and result["success"]

    def request_resume(self) -> None:
        self._commands.put(("resume",))

    def request_nvml_fallback(self, enabled: bool) -> None:
        self._commands.put(("nvml_fallback", bool(enabled)))

    def request_thermal_protection(
        self,
        enabled: bool,
        cpu_critical_c: int,
        gpu_critical_c: int,
    ) -> None:
        self._commands.put(
            (
                "thermal_protection_settings",
                bool(enabled),
                int(cpu_critical_c),
                int(gpu_critical_c),
            )
        )

    def stop(self) -> None:
        self._stop_event.set()

    def _process_command(self, command: tuple) -> None:
        name = command[0]
        if name == "mode":
            if len(command) == 3:  # v1 test compatibility
                generation, mode, linked = (
                    self.controller.command_generation,
                    command[1],
                    command[2],
                )
            else:
                generation, mode, linked = command[1], command[2], command[3]
            if generation != self.controller.command_generation:
                return
            if mode is ControlMode.MANUAL:
                # Take a fresh reading at the transition boundary. A cached poll
                # can be stale after Auto has been slowing the fans down.
                sample = self.controller.read_telemetry()
                self.telemetry.emit(sample)
                targets = self.controller.enter_manual_from_actual(
                    sample.fan1_rpm, sample.fan2_rpm, linked
                )
                self.manual_initialized.emit(list(targets))
            else:
                self.controller.set_mode(mode)
            self.mode_changed.emit(self.controller.mode.value)
            self.targets_changed.emit(list(self.controller.target_rpm))
        elif name == "manual":
            if len(command) == 3:
                generation, fan1, fan2 = (
                    self.controller.command_generation,
                    command[1],
                    command[2],
                )
            else:
                generation, fan1, fan2 = command[1], command[2], command[3]
            if generation != self.controller.command_generation:
                return
            self.controller.set_manual_targets(fan1, fan2)
            self.targets_changed.emit(list(self.controller.target_rpm))
        elif name in ("curve", "curves"):
            if name == "curve":
                self.controller.set_custom_curves(
                    command[1], self.controller.custom_curves[1]
                )
            else:
                self.controller.set_custom_curves(command[1], command[2])
            activate = name == "curve" or len(command) < 4 or bool(command[3])
            if activate:
                self.controller.set_profile(CurveProfile.CUSTOM)
                self.profile_changed.emit(self.controller.profile.value)
        elif name == "profile":
            if command[1] != self.controller.command_generation:
                return
            self.controller.set_profile(command[2])
            self.profile_changed.emit(self.controller.profile.value)
            self.targets_changed.emit(list(self.controller.target_rpm))
        elif name == "profile_data":
            self.controller.set_profile(command[1])
            self.profile_changed.emit(self.controller.profile.value)
        elif name == "suspend":
            completed = command[1] if len(command) > 1 else None
            result = command[2] if len(command) > 2 else {"success": False}
            try:
                result["success"] = self.controller.suspend()
                self._suspended = True
                self.mode_changed.emit(self.controller.mode.value)
                self.targets_changed.emit(list(self.controller.target_rpm))
            finally:
                if completed is not None:
                    completed.set()
        elif name == "resume":
            self.controller.restore_auto(reason="resume")
            self._suspended = False
            self.mode_changed.emit(self.controller.mode.value)
            self.targets_changed.emit(list(self.controller.target_rpm))
        elif name == "nvml_fallback":
            if hasattr(self.gpu_provider, "fallback_enabled"):
                self.gpu_provider.fallback_enabled = bool(command[1])
        elif name == "thermal_protection_settings":
            enabled, cpu_critical_c, gpu_critical_c = command[1:]
            self.thermal_policy.configure(
                enabled,
                cpu_critical_c,
                gpu_critical_c,
            )
            configure_remote = getattr(
                self.controller, "configure_thermal_protection", None
            )
            if configure_remote is not None:
                configure_remote(enabled, cpu_critical_c, gpu_critical_c)

    def _handle_sample(self, raw: Telemetry, now: float) -> TelemetrySnapshot:
        cpu = cpu_temperature_reading(raw.cpu_c, now)
        gpu = self.gpu_provider.resolve(raw.gpu_c, now)
        snapshot = TelemetrySnapshot(raw, cpu, gpu)
        status = self.thermal_policy.evaluate(cpu, gpu)
        self.thermal_status.emit(status)

        protection_needs_restore = (
            self.thermal_policy.enabled
            and status.critical
            and (
                not self.controller.thermal_latched
                or self.controller.mode is not ControlMode.LENOVO_AUTO
            )
            and (
                self._last_protection_attempt is None
                or now - self._last_protection_attempt >= 2.0
            )
        )
        if protection_needs_restore:
            self._last_protection_attempt = now
            reason = "critical " + "/".join(status.critical_components) + " temperature"
            restored = self.controller.trigger_thermal_protection(reason)
            self.protection_triggered.emit(reason, restored)
            self.mode_changed.emit(self.controller.mode.value)
            self.targets_changed.emit(list(self.controller.target_rpm))
            return snapshot

        if self.controller.mode is not ControlMode.LENOVO_AUTO:
            if not cpu.valid:
                self.controller.restore_auto(reason="CPU sensor unavailable")
                self.operation_error.emit(
                    "CPU sensor unavailable; Lenovo Auto was restored."
                )
                self.mode_changed.emit(self.controller.mode.value)
                self.targets_changed.emit(list(self.controller.target_rpm))
                return snapshot
            if not gpu.valid and not gpu.sleeping:
                self.controller.restore_auto(reason="GPU monitoring unavailable")
                self.operation_error.emit(
                    "GPU monitoring could not be restored; Lenovo Auto was restored."
                )
                self.mode_changed.emit(self.controller.mode.value)
                self.targets_changed.emit(list(self.controller.target_rpm))
                return snapshot

        if self.controller.mode is ControlMode.FAN_CURVES:
            gpu_value = gpu.value_c if gpu.valid else None
            self.controller.apply_curves(cpu.value_c, gpu_value, now=now)
        return snapshot

    def run(self) -> None:
        startup_auto_pending = False
        try:
            try:
                self.controller.initialize()
                self.connection_changed.emit(True, "Connected")
                self.mode_changed.emit(self.controller.mode.value)
                self.targets_changed.emit(list(self.controller.target_rpm))
            except Exception as exc:
                startup_auto_pending = True
                self.connection_changed.emit(False, str(exc))
                self.operation_error.emit(str(exc))

            next_poll = time.monotonic()
            next_heartbeat = next_poll
            while not self._stop_event.is_set():
                try:
                    while True:
                        self._process_command(self._commands.get_nowait())
                except queue.Empty:
                    pass
                except Exception as exc:
                    self.log.error("Worker command failed: %s", exc)
                    self.operation_error.emit(str(exc))
                    self.mode_changed.emit(self.controller.mode.value)

                now = time.monotonic()
                if now >= next_heartbeat and hasattr(self.controller, "heartbeat"):
                    try:
                        self.controller.heartbeat()
                    except Exception as exc:
                        self.connection_changed.emit(False, str(exc))
                    next_heartbeat = now + 2.0
                if not self._suspended and now >= next_poll:
                    try:
                        if startup_auto_pending:
                            self.controller.initialize()
                            startup_auto_pending = False
                            self.mode_changed.emit(self.controller.mode.value)
                            self.targets_changed.emit(list(self.controller.target_rpm))
                        raw: Telemetry = self.controller.poll()
                        sample = self._handle_sample(raw, now)
                        self.telemetry.emit(sample)
                        if self.controller.writes_blocked:
                            self.connection_changed.emit(
                                False,
                                self.controller.last_error
                                or "EC writes are blocked pending recovery",
                            )
                        else:
                            self.connection_changed.emit(True, "Connected")
                        self.targets_changed.emit(list(self.controller.target_rpm))
                    except Exception as exc:
                        startup_auto_pending = True
                        self.connection_changed.emit(False, str(exc))
                    next_poll = now + self.interval
                self._stop_event.wait(0.04)
        finally:
            self.gpu_provider.close()
            try:
                self.controller.shutdown()
            except Exception as exc:
                self.log.error("Shutdown Auto Restore failed: %s", exc)
                self.operation_error.emit(f"Auto Restore on shutdown failed: {exc}")
