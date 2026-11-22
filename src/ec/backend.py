"""Hardware abstraction used by the Lenovo Gen 8 EC protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod


class EcBackend(ABC):
    """Minimal byte-I/O interface required by the EC protocol.

    Implementations are deliberately session based.  A caller opens a backend,
    performs a batch of EC operations, and closes it immediately afterwards.
    """

    @abstractmethod
    def open(self) -> None:
        """Open the privileged I/O session."""

    @abstractmethod
    def close(self) -> None:
        """Close the session and release all driver resources."""

    @abstractmethod
    def read_io_port_byte(self, port: int) -> int:
        """Read one byte from an I/O port."""

    @abstractmethod
    def write_io_port_byte(self, port: int, value: int) -> None:
        """Write one byte to an I/O port."""

    def __enter__(self) -> "EcBackend":
        self.open()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()
