from __future__ import annotations

import threading
import unittest

from control.controller import CommunicationBlockedError, ControlStateError, FanController
from control.fan_curve import CurveProfile
from ec.lenovo_gen8_ec import EcResponseError, EcTimeoutError, LenovoGen8Ec
from ec.mock_backend import MockEcBackend
from ec.protocol import (
    CPU_TEMPERATURE,
    EC_COMMAND_PORT,
    EC_DATA_PORT,
    FAN_1_TACH,
    FAN_2_TACH,
    FCMO,
    GPU_TEMPERATURE,
    ControlMode,
    clamp_rpm,
    fcmo_name,
    normalize_actual_rpm,
    normalize_target_rpm,
    raw_to_rpm,
    rpm_to_raw,
)
from services.telemetry_worker import TelemetryWorker


def write_events(backend: MockEcBackend) -> list[tuple]:
    return [event for event in backend.events if event[0] == "write"]


def telemetry_registers() -> dict[int, int]:
    return {
        CPU_TEMPERATURE: 61,
        GPU_TEMPERATURE: 55,
        FCMO: 0,
        FAN_1_TACH: 24,
        FAN_2_TACH: 31,
    }


class ProtocolTests(unittest.TestCase):
    def test_lram_transaction_sequence(self) -> None:
        backend = MockEcBackend({CPU_TEMPERATURE: 67})
        with backend:
            value = LenovoGen8Ec(backend, timeout=0.01).read_lram(CPU_TEMPERATURE)
        self.assertEqual(value, 67)
        self.assertEqual(
            write_events(backend),
            [
                ("write", EC_COMMAND_PORT, 0x7E),
                ("write", EC_DATA_PORT, 0x04),
                ("write", EC_DATA_PORT, 0xB0),
            ],
        )

    def test_fan_command_sequence_and_ack(self) -> None:
        backend = MockEcBackend()
        with backend:
            LenovoGen8Ec(backend, timeout=0.01).set_fan(1, 30)
        self.assertEqual(
            write_events(backend),
            [
                ("write", EC_COMMAND_PORT, 0xEF),
                ("write", EC_DATA_PORT, 0x61),
                ("write", EC_DATA_PORT, 30),
            ],
        )
        self.assertEqual(backend.fan_targets[1], 30)

    def test_invalid_ack_is_rejected(self) -> None:
        backend = MockEcBackend()
        backend.command_response = 0x00
        with backend, self.assertRaisesRegex(EcResponseError, "expected 0xAC"):
            LenovoGen8Ec(backend, timeout=0.01).set_fan(2, 31)

    def test_raw_rpm_conversion(self) -> None:
        for raw, rpm in [(0, 0), (18, 1800), (24, 2400), (30, 3000), (53, 5300)]:
            with self.subTest(raw=raw):
                self.assertEqual(raw_to_rpm(raw), rpm)

    def test_manual_limits(self) -> None:
        for rpm in [1800, 2400, 3000, 5300]:
            with self.subTest(valid=rpm):
                self.assertEqual(rpm_to_raw(rpm), rpm // 100)
        for rpm in [0, 1700, 1750, 5400, 6000]:
            with self.subTest(invalid=rpm), self.assertRaises(ValueError):
                rpm_to_raw(rpm)

    def test_rpm_snapping_uses_nearest_100_with_half_up_ties(self) -> None:
        self.assertEqual(clamp_rpm(3449), 3400)
        self.assertEqual(clamp_rpm(3450), 3500)
        self.assertEqual(clamp_rpm(3451), 3500)

    def test_actual_and_target_normalization_have_separate_ranges(self) -> None:
        self.assertEqual(normalize_actual_rpm(0), 0.0)
        self.assertAlmostEqual(normalize_actual_rpm(2650), 0.5)
        self.assertEqual(normalize_actual_rpm(9000), 1.0)
        self.assertEqual(normalize_target_rpm(1800), 0.0)
        self.assertEqual(normalize_target_rpm(5300), 1.0)

    def test_fcmo_mapping_and_unknown_value(self) -> None:
        self.assertEqual(fcmo_name(0), "Intelligent")
        self.assertEqual(fcmo_name(1), "Performance")
        self.assertEqual(fcmo_name(2), "Battery Saving")
        self.assertEqual(fcmo_name(3), "APF (Auto Performance)")
        self.assertEqual(fcmo_name(4), "AQT (Auto Quiet)")
        self.assertEqual(fcmo_name(255), "Unknown (255)")

    def test_ec_timeout_is_finite(self) -> None:
        backend = MockEcBackend()
        backend.force_ibf_timeout = True
        with backend, self.assertRaisesRegex(EcTimeoutError, "IBF timeout"):
            LenovoGen8Ec(
                backend, timeout=0.003, poll_interval=0.0001
            ).read_lram(CPU_TEMPERATURE)

    def test_all_controller_access_is_serialized(self) -> None:
        backend = MockEcBackend(telemetry_registers())
        backend.operation_delay = 0.01
        controller = FanController(lambda: backend, ec_timeout=0.02)
        errors: list[Exception] = []

        def read() -> None:
            try:
                controller.read_telemetry()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=read) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertFalse(errors)
        self.assertEqual(backend.max_concurrent_sessions, 1)

    def test_write_failure_blocks_until_telemetry_recovers(self) -> None:
        backend = MockEcBackend(telemetry_registers())
        controller = FanController(lambda: backend, ec_timeout=0.003)
        controller.initialize()
        controller.set_mode(ControlMode.MANUAL)
        backend.force_obf_timeout = True
        with self.assertRaises(EcTimeoutError):
            controller.set_manual_targets(3000, 3000)
        with self.assertRaises(CommunicationBlockedError):
            controller.set_manual_targets(3100, 3100)
        backend.force_obf_timeout = False
        controller.read_telemetry()
        self.assertIs(controller.mode, ControlMode.LENOVO_AUTO)
        controller.set_mode(ControlMode.MANUAL)
        controller.set_manual_targets(3100, 3100)
        self.assertEqual(controller.target_rpm, [3100, 3100])

    def test_same_manual_target_is_not_resent(self) -> None:
        backend = MockEcBackend(telemetry_registers())
        controller = FanController(lambda: backend)
        controller.initialize()
        controller.set_mode(ControlMode.MANUAL)
        controller.set_manual_targets(3000, 3100)
        first_write_count = len(write_events(backend))
        controller.set_manual_targets(3000, 3100)
        self.assertEqual(len(write_events(backend)), first_write_count)

    def test_curve_writes_only_when_effective_target_changes(self) -> None:
        backend = MockEcBackend(telemetry_registers())
        controller = FanController(lambda: backend)
        controller.initialize()
        controller.set_mode(ControlMode.FAN_CURVES)
        controller.set_profile(CurveProfile.BALANCED)
        first = controller.apply_curves(70, 45, now=0)
        self.assertEqual(first.effective_rpm, 3200)
        first_write_count = len(write_events(backend))
        controller.apply_curves(70, 45, now=1)
        self.assertEqual(len(write_events(backend)), first_write_count)
        hotter = controller.apply_curves(70, 80, now=2)
        self.assertEqual(hotter.effective_rpm, 5100)
        self.assertEqual(len(write_events(backend)), first_write_count + 6)

    def test_manual_reentry_picks_up_actual_rpm_not_saved_target(self) -> None:
        backend = MockEcBackend(telemetry_registers())
        controller = FanController(lambda: backend)
        controller.initialize()
        controller.set_mode(ControlMode.MANUAL)
        controller.set_manual_targets(5300, 5300)
        controller.set_mode(ControlMode.LENOVO_AUTO)

        targets = controller.enter_manual_from_actual(3410, 3570, linked=False)

        self.assertEqual(targets, (3400, 3600))
        self.assertEqual(controller.target_rpm, [3400, 3600])
        self.assertEqual(backend.fan_targets, {1: 34, 2: 36})

    def test_linked_manual_reentry_uses_rounded_actual_average(self) -> None:
        backend = MockEcBackend(telemetry_registers())
        controller = FanController(lambda: backend)
        controller.initialize()
        controller.set_mode(ControlMode.MANUAL)
        controller.set_manual_targets(5300, 5300)
        controller.set_mode(ControlMode.LENOVO_AUTO)

        targets = controller.enter_manual_from_actual(3400, 3500, linked=True)

        self.assertEqual(targets, (3500, 3500))
        self.assertEqual(backend.fan_targets, {1: 35, 2: 35})

    def test_worker_reads_fresh_actual_rpm_when_entering_manual(self) -> None:
        registers = telemetry_registers()
        backend = MockEcBackend(registers)
        controller = FanController(lambda: backend)
        controller.initialize()
        controller.set_mode(ControlMode.MANUAL)
        controller.set_manual_targets(5300, 5300)
        controller.set_mode(ControlMode.LENOVO_AUTO)
        backend.registers[FAN_1_TACH] = 34
        backend.registers[FAN_2_TACH] = 36
        initialized: list[list[int]] = []
        worker = TelemetryWorker(controller)
        worker.manual_initialized.connect(initialized.append)

        worker._process_command(("mode", ControlMode.MANUAL, False))

        self.assertEqual(initialized, [[3400, 3600]])
        self.assertEqual(backend.fan_targets, {1: 34, 2: 36})

    def test_telemetry_is_batched_in_one_session(self) -> None:
        backend = MockEcBackend(telemetry_registers())
        controller = FanController(lambda: backend)
        sample = controller.read_telemetry()
        self.assertEqual(
            (sample.cpu_c, sample.gpu_c, sample.fan1_rpm, sample.fan2_rpm),
            (61, 55, 2400, 3100),
        )
        self.assertEqual(backend.open_count, 1)
        self.assertEqual(backend.close_count, 1)

    def test_starting_state_rejects_custom_control_until_auto_confirmed(self) -> None:
        backend = MockEcBackend(telemetry_registers())
        controller = FanController(lambda: backend)
        with self.assertRaises(ControlStateError):
            controller.set_mode(ControlMode.MANUAL)
        self.assertEqual(write_events(backend), [])
        controller.initialize()
        controller.set_mode(ControlMode.MANUAL)

    def test_auto_restore_and_shutdown_use_confirmed_command(self) -> None:
        backend = MockEcBackend()
        controller = FanController(lambda: backend)
        controller.initialize()
        controller.set_mode(ControlMode.MANUAL)
        controller.set_manual_targets(3000, 3000)
        controller.shutdown()
        writes = write_events(backend)
        auto_triplet = [
            ("write", EC_COMMAND_PORT, 0xEF),
            ("write", EC_DATA_PORT, 0x63),
            ("write", EC_DATA_PORT, 0x03),
        ]
        self.assertEqual(writes[:3], auto_triplet)
        self.assertEqual(writes[-3:], auto_triplet)
        self.assertIs(controller.mode, ControlMode.LENOVO_AUTO)
        self.assertEqual(backend.open_count, backend.close_count)


if __name__ == "__main__":
    unittest.main()
