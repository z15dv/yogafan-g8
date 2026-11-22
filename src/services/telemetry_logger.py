"""Asynchronous CSV telemetry/event logging."""

from __future__ import annotations

import csv
from datetime import datetime
import os
from pathlib import Path
import queue
import shutil
import threading
import time
from typing import Mapping


CSV_FIELDS = (
    "timestamp",
    "elapsed_seconds",
    "cpu_c",
    "gpu_c",
    "gpu_source",
    "gpu_valid",
    "fcmo_raw",
    "lenovo_mode",
    "control_mode",
    "curve_profile",
    "fan1_actual_rpm",
    "fan2_actual_rpm",
    "fan1_target_rpm",
    "fan2_target_rpm",
    "ec_connected",
    "event",
)


def default_log_directory() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    return root / "YogaFan G8" / "logs"


class TelemetryCsvLogger:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = Path(directory or default_log_directory())
        self.path: Path | None = None
        self.last_error: str | None = None
        self._queue: queue.Queue[dict[str, object] | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._start_monotonic: float | None = None
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self, path: Path | None = None) -> Path:
        if self._active and self.path is not None:
            return self.path
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = Path(path) if path else self.directory / (
            "telemetry_" + datetime.now().astimezone().strftime("%Y%m%d_%H%M%S") + ".csv"
        )
        self.last_error = None
        self._start_monotonic = time.monotonic()
        self._active = True
        self._thread = threading.Thread(
            target=self._writer,
            args=(self.path,),
            name="YogaFanCsvLogger",
            daemon=True,
        )
        self._thread.start()
        return self.path

    def log(self, values: Mapping[str, object]) -> None:
        if not self._active:
            return
        row = {field: values.get(field, "") for field in CSV_FIELDS}
        row["timestamp"] = values.get("timestamp") or datetime.now().astimezone().isoformat()
        if not values.get("elapsed_seconds") and self._start_monotonic is not None:
            row["elapsed_seconds"] = f"{time.monotonic() - self._start_monotonic:.3f}"
        self._queue.put(row)

    def event(self, event: str, **values: object) -> None:
        self.log({**values, "event": event})

    def stop(self, timeout: float = 3.0) -> None:
        if not self._active:
            return
        self._active = False
        self._queue.put(None)
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                self.last_error = "CSV logger did not stop before its deadline"
        self._thread = None

    def export_to(self, destination: Path) -> Path:
        if self.path is None or not self.path.exists():
            raise FileNotFoundError("No telemetry log has been created")
        destination = Path(destination)
        shutil.copy2(self.path, destination)
        return destination

    def _writer(self, path: Path) -> None:
        try:
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS)
                writer.writeheader()
                last_flush = time.monotonic()
                pending = 0
                while True:
                    try:
                        item = self._queue.get(timeout=0.5)
                    except queue.Empty:
                        item = ...
                    if item is None:
                        stream.flush()
                        return
                    if item is not ...:
                        writer.writerow(item)
                        pending += 1
                    now = time.monotonic()
                    if pending >= 10 or now - last_flush >= 1.0:
                        stream.flush()
                        pending = 0
                        last_flush = now
        except Exception as exc:
            self.last_error = str(exc)
            self._active = False
