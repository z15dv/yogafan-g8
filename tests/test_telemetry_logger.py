from __future__ import annotations

import csv
from pathlib import Path
import tempfile
import unittest

from services.telemetry_logger import CSV_FIELDS, TelemetryCsvLogger


class TelemetryLoggerTests(unittest.TestCase):
    @staticmethod
    def _temporary_directory():
        root = Path(__file__).resolve().parents[1] / ".test-output"
        root.mkdir(exist_ok=True)
        return tempfile.TemporaryDirectory(dir=root)

    def test_async_csv_separates_actual_target_and_missing_gpu(self) -> None:
        with self._temporary_directory() as directory:
            logger = TelemetryCsvLogger(Path(directory))
            path = logger.start()
            logger.log(
                {
                    "cpu_c": 60,
                    "gpu_c": "",
                    "gpu_source": "Unavailable",
                    "gpu_valid": False,
                    "fan1_actual_rpm": 2400,
                    "fan1_target_rpm": 5300,
                    "event": 'quoted, "event"',
                }
            )
            logger.stop()
            self.assertIsNone(logger.last_error)
            with path.open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(tuple(rows[0].keys()), CSV_FIELDS)
            self.assertEqual(rows[0]["gpu_c"], "")
            self.assertEqual(rows[0]["fan1_actual_rpm"], "2400")
            self.assertEqual(rows[0]["fan1_target_rpm"], "5300")
            self.assertEqual(rows[0]["event"], 'quoted, "event"')
            self.assertIn("+", rows[0]["timestamp"])

    def test_export_copies_completed_log(self) -> None:
        with self._temporary_directory() as directory:
            root = Path(directory)
            logger = TelemetryCsvLogger(root / "logs")
            logger.start()
            logger.event("test")
            logger.stop()
            destination = root / "export.csv"
            self.assertEqual(logger.export_to(destination), destination)
            self.assertTrue(destination.exists())


if __name__ == "__main__":
    unittest.main()
