"""In-memory EC backend used exclusively by automated tests."""

from __future__ import annotations

import threading
import time

from .backend import EcBackend
from .protocol import (
    EC_COMMAND_PORT,
    EC_DATA_PORT,
    FAN_COMMAND,
    FAN_QUERY,
    LRAM_READ_COMMAND,
    READ_FAN_1,
    READ_FAN_2,
    RESTORE_AUTO,
    SET_FAN_1,
    SET_FAN_2,
    STATUS_IBF,
    STATUS_OBF,
    SUCCESS_RESPONSE,
)


class MockEcBackend(EcBackend):
    """Small state-machine emulation; it never performs hardware I/O."""

    def __init__(self, registers: dict[int, int] | None = None) -> None:
        self.registers = dict(registers or {})
        self.fan_targets = {1: 30, 2: 30}
        self.events: list[tuple] = []
        self.responses: list[int] = []
        self.is_open = False
        self.open_count = 0
        self.close_count = 0
        self.force_ibf_timeout = False
        self.force_obf_timeout = False
        self.command_response = SUCCESS_RESPONSE
        self.operation_delay = 0.0
        self.max_concurrent_sessions = 0
        self._active_sessions = 0
        self._state: tuple = ("idle",)
        self._guard = threading.Lock()

    def open(self) -> None:
        with self._guard:
            self._active_sessions += 1
            self.max_concurrent_sessions = max(
                self.max_concurrent_sessions, self._active_sessions
            )
        self.open_count += 1
        self.is_open = True
        self.events.append(("open",))
        if self.operation_delay:
            time.sleep(self.operation_delay)

    def close(self) -> None:
        self.events.append(("close",))
        self.close_count += 1
        self.is_open = False
        with self._guard:
            self._active_sessions -= 1

    def read_io_port_byte(self, port: int) -> int:
        if not self.is_open:
            raise RuntimeError("Mock backend is closed")
        if port == EC_COMMAND_PORT:
            status = 0
            if self.force_ibf_timeout:
                status |= STATUS_IBF
            if self.responses and not self.force_obf_timeout:
                status |= STATUS_OBF
            self.events.append(("read", port, status))
            return status
        if port == EC_DATA_PORT:
            value = self.responses.pop(0) if self.responses else 0
            self.events.append(("read", port, value))
            return value
        raise ValueError(f"Unexpected mock port 0x{port:X}")

    def write_io_port_byte(self, port: int, value: int) -> None:
        if not self.is_open:
            raise RuntimeError("Mock backend is closed")
        value &= 0xFF
        self.events.append(("write", port, value))
        if port == EC_COMMAND_PORT:
            if value == LRAM_READ_COMMAND:
                self._state = ("lram_high",)
            elif value == FAN_COMMAND:
                self._state = ("fan_subcommand",)
            else:
                raise ValueError(f"Unexpected mock command 0x{value:02X}")
            return
        if port != EC_DATA_PORT:
            raise ValueError(f"Unexpected mock port 0x{port:X}")

        phase = self._state[0]
        if phase == "lram_high":
            self._state = ("lram_low", value)
        elif phase == "lram_low":
            address = (self._state[1] << 8) | value
            self.responses.append(self.registers.get(address, 0))
            self._state = ("idle",)
        elif phase == "fan_subcommand":
            self._state = ("fan_argument", value)
        elif phase == "fan_argument":
            subcommand = self._state[1]
            if subcommand == SET_FAN_1:
                self.fan_targets[1] = value
                self.responses.append(self.command_response)
            elif subcommand == SET_FAN_2:
                self.fan_targets[2] = value
                self.responses.append(self.command_response)
            elif subcommand == FAN_QUERY:
                if value == READ_FAN_1:
                    self.responses.append(self.fan_targets[1])
                elif value == READ_FAN_2:
                    self.responses.append(self.fan_targets[2])
                elif value == RESTORE_AUTO:
                    self.responses.append(self.command_response)
                else:
                    raise ValueError(f"Unexpected fan query 0x{value:02X}")
            else:
                raise ValueError(f"Unexpected fan subcommand 0x{subcommand:02X}")
            self._state = ("idle",)
        else:
            raise RuntimeError(f"Unexpected data write in state {phase}")
