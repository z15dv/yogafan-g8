from __future__ import annotations

import unittest

from control.controller import ControlStateError, FanController
from control.safety import ThermalLevel, ThermalProtectionPolicy
from ec.lenovo_gen8_ec import Telemetry
from ec.mock_backend import MockEcBackend
from ec.protocol import ControlMode
from services.telemetry_worker import TelemetryWorker
from services.temperature_provider import TemperatureReading


class FixedGpuProvider:
    def __init__(self, reading: TemperatureReading) -> None:
        self.reading = reading

    def resolve(self, _ec_value, _now):
        return self.reading

    def close(self) -> None:
        pass


def reading(value: int | None, source: str = "EC") -> TemperatureReading:
    return TemperatureReading(value, source, 0.0, value is not None)


class ThermalPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = ThermalProtectionPolicy()

    def test_exact_cpu_thresholds(self) -> None:
        self.assertEqual(
            self.policy.evaluate(reading(89), reading(None)).cpu_level,
            ThermalLevel.NORMAL,
        )
        self.assertEqual(
            self.policy.evaluate(reading(90), reading(None)).cpu_level,
            ThermalLevel.WARNING,
        )
        self.assertFalse(self.policy.evaluate(reading(94), reading(None)).critical)
        self.assertTrue(self.policy.evaluate(reading(95), reading(None)).critical)

    def test_exact_gpu_thresholds_and_missing_is_not_zero(self) -> None:
        self.assertEqual(
            self.policy.evaluate(reading(60), reading(82)).gpu_level,
            ThermalLevel.NORMAL,
        )
        self.assertEqual(
            self.policy.evaluate(reading(60), reading(83)).gpu_level,
            ThermalLevel.WARNING,
        )
        self.assertTrue(self.policy.evaluate(reading(60), reading(85)).critical)
        status = self.policy.evaluate(reading(60), reading(None, "Unavailable"))
        self.assertEqual(status.gpu_level, ThermalLevel.NORMAL)

    def test_custom_thresholds_and_disabled_handoff_are_explicit(self) -> None:
        self.policy.configure(False, 98, 88)
        status = self.policy.evaluate(reading(98), reading(88))
        self.assertTrue(status.critical)
        self.assertFalse(self.policy.enabled)
        self.assertEqual(self.policy.cpu_critical_c, 98)
        self.assertEqual(self.policy.gpu_critical_c, 88)

    def test_unsafe_threshold_ranges_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.policy.configure(True, 101, 85)
        with self.assertRaises(ValueError):
            self.policy.configure(True, 95, 79)


class ThermalIntegrationTests(unittest.TestCase):
    def _worker(self, gpu_c: int = 50):
        backend = MockEcBackend()
        controller = FanController(lambda: backend)
        controller.initialize()
        provider = FixedGpuProvider(reading(gpu_c))
        worker = TelemetryWorker(controller, gpu_provider=provider)
        return backend, controller, worker

    def test_cpu_critical_immediately_restores_auto_and_latches(self) -> None:
        _backend, controller, worker = self._worker()
        controller.set_mode(ControlMode.MANUAL)
        controller.set_manual_targets(3000, 3000)

        worker._handle_sample(Telemetry(95, 50, 0, 30, 30), now=1.0)

        self.assertIs(controller.mode, ControlMode.LENOVO_AUTO)
        self.assertTrue(controller.thermal_latched)
        with self.assertRaises(ControlStateError):
            controller.set_mode(ControlMode.MANUAL)

    def test_gpu_critical_immediately_restores_auto(self) -> None:
        _backend, controller, worker = self._worker(85)
        controller.set_mode(ControlMode.FAN_CURVES)
        worker._handle_sample(Telemetry(60, 0, 0, 30, 30), now=1.0)
        self.assertIs(controller.mode, ControlMode.LENOVO_AUTO)
        self.assertTrue(controller.thermal_latched)

    def test_failed_auto_restore_is_never_reported_as_success(self) -> None:
        backend, controller, worker = self._worker()
        controller.set_mode(ControlMode.MANUAL)
        results: list[tuple[str, bool]] = []
        worker.protection_triggered.connect(
            lambda reason, restored: results.append((reason, restored))
        )
        backend.command_response = 0
        worker._handle_sample(Telemetry(95, 50, 0, 30, 30), now=1.0)
        self.assertTrue(results)
        self.assertFalse(results[-1][1])
        self.assertTrue(controller.thermal_latched)

        backend.command_response = 0xAC
        worker._handle_sample(Telemetry(95, 50, 0, 30, 30), now=3.1)
        self.assertTrue(results[-1][1])
        self.assertIs(controller.mode, ControlMode.LENOVO_AUTO)

    def test_queued_manual_command_is_discarded_after_protection(self) -> None:
        _backend, controller, worker = self._worker()
        controller.set_mode(ControlMode.MANUAL)
        worker.request_manual(5000, 5000)
        controller.trigger_thermal_protection("test critical")
        command = worker._commands.get_nowait()
        worker._process_command(command)
        self.assertIs(controller.mode, ControlMode.LENOVO_AUTO)
        self.assertEqual(controller.target_rpm, [None, None])

    def test_curve_control_never_reactivates_after_protection(self) -> None:
        _backend, controller, worker = self._worker()
        controller.set_mode(ControlMode.FAN_CURVES)
        controller.trigger_thermal_protection("test critical")
        worker._handle_sample(Telemetry(60, 50, 0, 30, 30), now=20.0)
        self.assertIs(controller.mode, ControlMode.LENOVO_AUTO)
        self.assertEqual(controller.target_rpm, [None, None])

    def test_disabled_temperature_handoff_does_not_force_auto(self) -> None:
        _backend, controller, worker = self._worker()
        controller.set_mode(ControlMode.MANUAL)
        worker.thermal_policy.configure(False, 95, 85)
        worker._handle_sample(Telemetry(99, 50, 0, 30, 30), now=1.0)
        self.assertIs(controller.mode, ControlMode.MANUAL)
        self.assertFalse(controller.thermal_latched)


if __name__ == "__main__":
    unittest.main()
