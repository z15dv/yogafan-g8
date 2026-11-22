from __future__ import annotations

import unittest

from services.notifications import (
    TemperatureWarning,
    TemperatureWarningTracker,
    WarningLevel,
)


class TemperatureWarningTests(unittest.TestCase):
    def test_thresholds_notify_once_until_recrossed(self) -> None:
        tracker = TemperatureWarningTracker()
        self.assertEqual(tracker.update(89), (WarningLevel.NORMAL, []))
        self.assertEqual(
            tracker.update(90),
            (WarningLevel.HIGH, [TemperatureWarning("CPU", WarningLevel.HIGH, 90)]),
        )
        self.assertEqual(tracker.update(94), (WarningLevel.HIGH, []))
        self.assertEqual(
            tracker.update(95),
            (
                WarningLevel.CRITICAL,
                [TemperatureWarning("CPU", WarningLevel.CRITICAL, 95)],
            ),
        )
        self.assertEqual(tracker.update(99), (WarningLevel.CRITICAL, []))
        self.assertEqual(tracker.update(80), (WarningLevel.NORMAL, []))
        self.assertEqual(
            tracker.update(90),
            (WarningLevel.HIGH, [TemperatureWarning("CPU", WarningLevel.HIGH, 90)]),
        )

    def test_jump_over_both_thresholds_emits_both_notifications(self) -> None:
        tracker = TemperatureWarningTracker()
        self.assertEqual(
            tracker.update(96),
            (
                WarningLevel.CRITICAL,
                [
                    TemperatureWarning("CPU", WarningLevel.HIGH, 96),
                    TemperatureWarning("CPU", WarningLevel.CRITICAL, 96),
                ],
            ),
        )

    def test_threshold_jitter_does_not_repeat_notification(self) -> None:
        tracker = TemperatureWarningTracker()
        self.assertEqual(
            tracker.update(90),
            (WarningLevel.HIGH, [TemperatureWarning("CPU", WarningLevel.HIGH, 90)]),
        )
        self.assertEqual(tracker.update(89), (WarningLevel.NORMAL, []))
        self.assertEqual(tracker.update(90), (WarningLevel.HIGH, []))
        self.assertEqual(tracker.update(87), (WarningLevel.NORMAL, []))
        self.assertEqual(
            tracker.update(90),
            (WarningLevel.HIGH, [TemperatureWarning("CPU", WarningLevel.HIGH, 90)]),
        )

    def test_gpu_thresholds_are_independent_and_missing_is_not_zero(self) -> None:
        tracker = TemperatureWarningTracker()
        self.assertEqual(tracker.update(60, 82), (WarningLevel.NORMAL, []))
        self.assertEqual(
            tracker.update(60, 83),
            (WarningLevel.HIGH, [TemperatureWarning("GPU", WarningLevel.HIGH, 83)]),
        )
        self.assertEqual(
            tracker.update(60, 85),
            (
                WarningLevel.CRITICAL,
                [TemperatureWarning("GPU", WarningLevel.CRITICAL, 85)],
            ),
        )
        self.assertEqual(tracker.update(60, None), (WarningLevel.NORMAL, []))


if __name__ == "__main__":
    unittest.main()
