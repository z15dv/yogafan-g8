"""Lenovo Yoga Pro 9 14IRP8 Gen 8 EC protocol implementation."""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable

from .backend import EcBackend
from .protocol import (
    CPU_TEMPERATURE,
    EC_COMMAND_PORT,
    EC_DATA_PORT,
    FAN_1_TACH,
    FAN_2_TACH,
    FAN_COMMAND,
    FAN_QUERY,
    FCMO,
    GPU_TEMPERATURE,
    LRAM_READ_COMMAND,
    READ_FAN_1,
    READ_FAN_2,
    RESTORE_AUTO,
    SET_FAN_1,
    SET_FAN_2,
    STATUS_IBF,
    STATUS_OBF,
    SUCCESS_RESPONSE,
    fcmo_name,
    raw_to_rpm,
    validate_fan_raw,
)


class EcTimeoutError(TimeoutError):
    """A finite EC input/output buffer wait expired."""


class EcResponseError(RuntimeError):
    """The EC did not return the confirmed success byte."""


@dataclass(frozen=True)
class Telemetry:
    cpu_c: int
    gpu_c: int
    fcmo_raw: int
    fan1_raw: int
    fan2_raw: int

    @property
    def fan1_rpm(self) -> int:
        return raw_to_rpm(self.fan1_raw)

    @property
    def fan2_rpm(self) -> int:
        return raw_to_rpm(self.fan2_raw)

    @property
    def lenovo_mode(self) -> str:
        return fcmo_name(self.fcmo_raw)


class LenovoGen8Ec:
    """Confirmed `0x6C`/`0x68` protocol only; no experimental commands."""

    def __init__(
        self,
        backend: EcBackend,
        lock: threading.RLock | None = None,
        timeout: float = 0.5,
        poll_interval: float = 0.0005,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.backend = backend
        self._lock = lock or threading.RLock()
        self.timeout = timeout
        self.poll_interval = poll_interval
        self._monotonic = monotonic
        self._sleep = sleep

    def _status(self) -> int:
        return self.backend.read_io_port_byte(EC_COMMAND_PORT)

    def _wait_ibf_clear(self, stage: str) -> None:
        deadline = self._monotonic() + self.timeout
        while self._monotonic() < deadline:
            if not self._status() & STATUS_IBF:
                return
            self._sleep(self.poll_interval)
        raise EcTimeoutError(f"EC IBF timeout ({stage})")

    def _wait_obf_set(self, stage: str) -> None:
        deadline = self._monotonic() + self.timeout
        while self._monotonic() < deadline:
            if self._status() & STATUS_OBF:
                return
            self._sleep(self.poll_interval)
        raise EcTimeoutError(f"EC OBF timeout ({stage})")

    def _drain_stale_output(self) -> None:
        for _ in range(32):
            if not self._status() & STATUS_OBF:
                return
            self.backend.read_io_port_byte(EC_DATA_PORT)
            self._sleep(self.poll_interval)
        raise EcTimeoutError("EC output buffer could not be drained")

    def read_lram(self, address: int) -> int:
        if not 0 <= address <= 0xFFFF:
            raise ValueError("LRAM address must fit in 16 bits")
        high, low = (address >> 8) & 0xFF, address & 0xFF
        with self._lock:
            self._wait_ibf_clear("before LRAM read")
            self._drain_stale_output()
            self.backend.write_io_port_byte(EC_COMMAND_PORT, LRAM_READ_COMMAND)
            self._wait_ibf_clear("after LRAM command")
            self.backend.write_io_port_byte(EC_DATA_PORT, high)
            self._wait_ibf_clear("after LRAM high byte")
            self.backend.write_io_port_byte(EC_DATA_PORT, low)
            self._wait_ibf_clear("after LRAM low byte")
            self._wait_obf_set(f"LRAM 0x{address:04X}")
            return self.backend.read_io_port_byte(EC_DATA_PORT)

    def mailbox(self, subcommand: int, argument: int) -> int:
        with self._lock:
            self._wait_ibf_clear("before fan command")
            self._drain_stale_output()
            self.backend.write_io_port_byte(EC_COMMAND_PORT, FAN_COMMAND)
            self._wait_ibf_clear("after fan command")
            self.backend.write_io_port_byte(EC_DATA_PORT, subcommand)
            self._wait_ibf_clear("after fan subcommand")
            self.backend.write_io_port_byte(EC_DATA_PORT, argument)
            self._wait_ibf_clear("after fan argument")
            self._wait_obf_set("fan command response")
            return self.backend.read_io_port_byte(EC_DATA_PORT)

    def _require_success(self, response: int, operation: str) -> None:
        if response != SUCCESS_RESPONSE:
            raise EcResponseError(
                f"{operation} returned 0x{response:02X}; expected 0x{SUCCESS_RESPONSE:02X}"
            )

    def set_fan(self, fan: int, raw_value: int) -> None:
        raw_value = validate_fan_raw(raw_value)
        if fan not in (1, 2):
            raise ValueError("Fan number must be 1 or 2")
        subcommand = SET_FAN_1 if fan == 1 else SET_FAN_2
        self._require_success(self.mailbox(subcommand, raw_value), f"Set Fan {fan}")

    def read_fan_target(self, fan: int) -> int:
        if fan not in (1, 2):
            raise ValueError("Fan number must be 1 or 2")
        argument = READ_FAN_1 if fan == 1 else READ_FAN_2
        return self.mailbox(FAN_QUERY, argument)

    def restore_auto(self) -> None:
        response = self.mailbox(FAN_QUERY, RESTORE_AUTO)
        self._require_success(response, "Lenovo Auto Restore")

    def read_telemetry(self) -> Telemetry:
        """Read every displayed value during one already-open backend session."""
        with self._lock:
            return Telemetry(
                cpu_c=self.read_lram(CPU_TEMPERATURE),
                gpu_c=self.read_lram(GPU_TEMPERATURE),
                fcmo_raw=self.read_lram(FCMO),
                fan1_raw=self.read_lram(FAN_1_TACH),
                fan2_raw=self.read_lram(FAN_2_TACH),
            )
