from __future__ import annotations

import unittest
from unittest.mock import Mock

from control.controller import FanController
from ec.mock_backend import MockEcBackend
from ec.protocol import (
    CPU_TEMPERATURE,
    FAN_1_TACH,
    FAN_2_TACH,
    FCMO,
    GPU_TEMPERATURE,
    ControlMode,
)
from services.safety_service import (
    RemoteFanController,
    SafetyServiceCore,
    SafetyServiceServer,
    ServiceBusyError,
)
from services.windows_security import RestrictedAuthenticatedListener


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class TimedOutConnection:
    def __init__(self) -> None:
        self.closed = False

    def send(self, _value) -> None:
        pass

    def poll(self, _timeout) -> bool:
        return False

    def close(self) -> None:
        self.closed = True


def registers():
    return {
        CPU_TEMPERATURE: 60,
        GPU_TEMPERATURE: 50,
        FCMO: 0,
        FAN_1_TACH: 30,
        FAN_2_TACH: 31,
    }


class SafetyServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.backend = MockEcBackend(registers())
        self.controller = FanController(lambda: self.backend)
        self.core = SafetyServiceCore(
            self.controller, lease_timeout=6.0, monotonic=self.clock
        )

    def test_valid_heartbeats_keep_manual_session_alive(self) -> None:
        self.core.connect("one")
        self.core.dispatch("one", "set_mode", {"mode": ControlMode.MANUAL.value})
        self.core.dispatch(
            "one", "set_manual_targets", {"fan1_rpm": 3000, "fan2_rpm": 3100}
        )
        for moment in (2.0, 4.0, 6.0, 8.0):
            self.clock.now = moment
            self.core.heartbeat("one")
            self.assertFalse(self.core.tick())
        self.assertIs(self.controller.mode, ControlMode.MANUAL)

    def test_missing_heartbeat_restores_auto(self) -> None:
        self.core.connect("one")
        self.core.dispatch("one", "set_mode", {"mode": ControlMode.MANUAL.value})
        self.core.dispatch(
            "one", "set_manual_targets", {"fan1_rpm": 3000, "fan2_rpm": 3000}
        )
        self.clock.now = 6.01
        self.assertTrue(self.core.tick())
        self.assertIs(self.controller.mode, ControlMode.LENOVO_AUTO)
        self.assertIsNone(self.core.active_client)
        self.assertTrue(self.core.events[-1].success)

    def test_service_thermal_poll_protects_without_gui_temperature_message(self) -> None:
        self.core.connect("one")
        self.core.dispatch("one", "set_mode", {"mode": ControlMode.MANUAL.value})
        self.backend.registers[CPU_TEMPERATURE] = 95
        self.clock.now = 1.1
        self.core.heartbeat("one")
        self.assertTrue(self.core.tick())
        self.assertIs(self.controller.mode, ControlMode.LENOVO_AUTO)
        self.assertTrue(self.controller.thermal_latched)
        self.assertIsNone(self.core.active_client)

    def test_service_honors_valid_custom_threshold_and_disable_setting(self) -> None:
        self.core.connect("one")
        configured = self.core.dispatch(
            "one",
            "configure_thermal_protection",
            {"enabled": True, "cpu_critical_c": 98, "gpu_critical_c": 88},
        )
        self.assertEqual(configured["thermal_protection"]["cpu_critical_c"], 98)
        self.core.dispatch("one", "set_mode", {"mode": ControlMode.MANUAL.value})
        self.backend.registers[CPU_TEMPERATURE] = 97
        self.clock.now = 1.1
        self.core.heartbeat("one")
        self.assertFalse(self.core.tick())
        self.core.dispatch(
            "one",
            "configure_thermal_protection",
            {"enabled": False, "cpu_critical_c": 98, "gpu_critical_c": 88},
        )
        self.backend.registers[CPU_TEMPERATURE] = 100
        self.clock.now = 2.2
        self.core.heartbeat("one")
        self.assertFalse(self.core.tick())
        self.assertIs(self.controller.mode, ControlMode.MANUAL)

    def test_service_rejects_out_of_range_thermal_threshold(self) -> None:
        self.core.connect("one")
        with self.assertRaises(ValueError):
            self.core.dispatch(
                "one",
                "configure_thermal_protection",
                {"enabled": True, "cpu_critical_c": 110, "gpu_critical_c": 85},
            )

    def test_service_uses_fresh_ec_cpu_for_curve_request(self) -> None:
        self.core.connect("one")
        self.core.dispatch("one", "set_mode", {"mode": ControlMode.FAN_CURVES.value})
        self.backend.registers[CPU_TEMPERATURE] = 80
        result = self.core.dispatch(
            "one",
            "apply_curves",
            {"cpu_c": 40, "gpu_c": 45},
        )
        self.assertEqual(result.cpu_request_rpm, 4200)
        self.assertEqual(result.effective_rpm, 4200)

    def test_forced_mock_gui_termination_recovers_on_lost_connection(self) -> None:
        self.core.connect("gui-process")
        self.core.dispatch(
            "gui-process", "set_mode", {"mode": ControlMode.MANUAL.value}
        )
        restored = self.core.disconnect("gui-process", "mock process terminated")
        self.assertTrue(restored)
        self.assertIs(self.controller.mode, ControlMode.LENOVO_AUTO)

    def test_duplicate_clients_cannot_control_concurrently(self) -> None:
        self.core.connect("one")
        with self.assertRaises(ServiceBusyError):
            self.core.connect("two")

    def test_new_session_always_begins_in_auto(self) -> None:
        self.core.connect("one")
        self.core.dispatch("one", "set_mode", {"mode": ControlMode.MANUAL.value})
        self.core.disconnect("one")
        self.core.connect("two")
        self.assertIs(self.controller.mode, ControlMode.LENOVO_AUTO)

    def test_fresh_session_clears_latch_but_never_reactivates_old_mode(self) -> None:
        self.core.connect("one")
        self.core.dispatch("one", "set_mode", {"mode": ControlMode.MANUAL.value})
        self.controller.trigger_thermal_protection("mock critical")
        self.assertTrue(self.controller.thermal_latched)
        self.core.disconnect("one")
        self.core.connect("two")
        self.assertFalse(self.controller.thermal_latched)
        self.assertIs(self.controller.mode, ControlMode.LENOVO_AUTO)

    def test_restore_failure_is_recorded_as_failure(self) -> None:
        self.core.connect("one")
        self.backend.command_response = 0
        self.clock.now = 7
        self.assertTrue(self.core.tick())
        self.assertFalse(self.core.events[-1].success)
        self.assertIn("expected 0xAC", self.core.events[-1].detail)

    def test_shutdown_runs_bounded_restore(self) -> None:
        self.core.connect("one")
        self.assertTrue(self.core.shutdown(attempts=2))
        self.assertEqual(self.core.events[-1].event, "service_shutdown_auto_restore")

    def test_client_request_timeout_fails_closed(self) -> None:
        remote = RemoteFanController(b"x" * 32, request_timeout=0.01)
        connection = TimedOutConnection()
        remote._connection = connection
        with self.assertRaises(TimeoutError):
            remote.read_telemetry()
        self.assertTrue(connection.closed)
        self.assertTrue(remote.writes_blocked)

    def test_remote_sends_thermal_configuration_to_service(self) -> None:
        status = self.core.connect("one")
        remote = RemoteFanController(b"x" * 32)
        remote._request = Mock(return_value=status)
        remote.configure_thermal_protection(False, 98, 88)
        remote._request.assert_called_once_with(
            "configure_thermal_protection",
            {
                "enabled": False,
                "cpu_critical_c": 98,
                "gpu_critical_c": 88,
            },
        )

    def test_server_defaults_to_creation_time_restricted_pipe_acl(self) -> None:
        server = SafetyServiceServer(self.core, b"x" * 48)
        self.assertIs(
            server._listener_factory,
            RestrictedAuthenticatedListener,
        )


if __name__ == "__main__":
    unittest.main()
