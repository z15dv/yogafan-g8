from __future__ import annotations

import multiprocessing
import os
import unittest

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
from services.safety_service import SafetyServiceCore


def _mock_service_process(connection, result_queue) -> None:
    backend = MockEcBackend(
        {
            CPU_TEMPERATURE: 60,
            GPU_TEMPERATURE: 50,
            FCMO: 0,
            FAN_1_TACH: 30,
            FAN_2_TACH: 31,
        }
    )
    controller = FanController(lambda: backend)
    core = SafetyServiceCore(controller)
    core.connect("mock-gui")
    try:
        while True:
            command, payload = connection.recv()
            core.dispatch("mock-gui", command, payload)
    except EOFError:
        restored = core.disconnect("mock-gui", "mock GUI process terminated")
        result_queue.put(
            {
                "restored": restored,
                "mode": controller.mode.value,
                "targets": controller.target_rpm,
                "event": core.events[-1].event,
            }
        )
    finally:
        connection.close()


def _abrupt_mock_gui(connection, ready) -> None:
    connection.send(("set_mode", {"mode": ControlMode.MANUAL.value}))
    connection.send(
        ("set_manual_targets", {"fan1_rpm": 3000, "fan2_rpm": 3100})
    )
    ready.set()
    # Deliberately bypass disconnect/finally, mirroring Task Manager termination.
    os._exit(0)


class SafetyServiceProcessTests(unittest.TestCase):
    def test_abrupt_gui_process_exit_triggers_mock_service_restore(self) -> None:
        context = multiprocessing.get_context("spawn")
        service_end, gui_end = context.Pipe(duplex=True)
        result_queue = context.Queue()
        ready = context.Event()
        service = context.Process(
            target=_mock_service_process, args=(service_end, result_queue)
        )
        gui = context.Process(target=_abrupt_mock_gui, args=(gui_end, ready))
        service.start()
        gui.start()
        service_end.close()
        gui_end.close()
        try:
            self.assertTrue(ready.wait(5.0))
            gui.join(5.0)
            self.assertFalse(gui.is_alive())
            service.join(5.0)
            self.assertFalse(service.is_alive())
            result = result_queue.get(timeout=2.0)
            self.assertTrue(result["restored"])
            self.assertEqual(result["mode"], ControlMode.LENOVO_AUTO.value)
            self.assertEqual(result["targets"], [None, None])
            self.assertEqual(result["event"], "watchdog_auto_restore")
        finally:
            if gui.is_alive():
                gui.terminate()
            if service.is_alive():
                service.terminate()
            gui.join(2.0)
            service.join(2.0)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    unittest.main()
