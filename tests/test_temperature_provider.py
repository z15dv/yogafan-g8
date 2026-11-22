from __future__ import annotations

import unittest

from services.temperature_provider import (
    GpuTemperatureProvider,
    TemperatureReading,
)


class FakeNvml:
    def __init__(self, readings: list[TemperatureReading]) -> None:
        self.readings = list(readings)
        self.calls = 0
        self.closed = False

    def read(self, _now=None) -> TemperatureReading:
        self.calls += 1
        return self.readings.pop(0)

    def close(self) -> None:
        self.closed = True


class TemperatureProviderTests(unittest.TestCase):
    def test_valid_ec_reading_never_touches_nvml(self) -> None:
        nvml = FakeNvml([TemperatureReading(55, "NVML", 1.0, True)])
        provider = GpuTemperatureProvider(nvml, monotonic=lambda: 1.0)

        reading = provider.resolve(52)

        self.assertEqual((reading.value_c, reading.source), (52, "EC"))
        self.assertEqual(nvml.calls, 0)

    def test_invalid_ec_uses_throttled_nvml_fallback(self) -> None:
        nvml = FakeNvml([TemperatureReading(61, "NVML", 10.0, True)])
        provider = GpuTemperatureProvider(
            nvml, fallback_interval=10.0, monotonic=lambda: 10.0
        )

        first = provider.resolve(0, 10.0)
        second = provider.resolve(0, 12.0)

        self.assertEqual(first.value_c, 61)
        self.assertEqual(second.value_c, 61)
        self.assertEqual(nvml.calls, 1)

    def test_recent_valid_reading_has_bounded_grace_then_becomes_unavailable(self) -> None:
        nvml = FakeNvml(
            [TemperatureReading.unavailable(2.0, "GPU sleeping", sleeping=True)]
        )
        provider = GpuTemperatureProvider(
            nvml,
            fallback_interval=20.0,
            grace_period=3.0,
            monotonic=lambda: 1.0,
        )
        provider.resolve(48, 1.0)

        grace = provider.resolve(0, 2.0)
        unavailable = provider.resolve(0, 5.1)

        self.assertTrue(grace.valid)
        self.assertTrue(grace.grace)
        self.assertFalse(unavailable.valid)
        self.assertTrue(unavailable.sleeping)

    def test_disabled_fallback_does_not_treat_missing_gpu_as_zero(self) -> None:
        nvml = FakeNvml([])
        provider = GpuTemperatureProvider(nvml, fallback_enabled=False)

        reading = provider.resolve(0, 5.0)

        self.assertFalse(reading.valid)
        self.assertIsNone(reading.value_c)
        self.assertEqual(nvml.calls, 0)

    def test_close_releases_nvml_provider(self) -> None:
        nvml = FakeNvml([])
        provider = GpuTemperatureProvider(nvml)
        provider.close()
        self.assertTrue(nvml.closed)


if __name__ == "__main__":
    unittest.main()
