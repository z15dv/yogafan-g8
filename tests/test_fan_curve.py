from __future__ import annotations

import unittest

from control.fan_curve import (
    BALANCED_CPU_CURVE,
    BALANCED_GPU_CURVE,
    BUILTIN_PROFILE_CURVES,
    PERFORMANCE_CPU_CURVE,
    PERFORMANCE_GPU_CURVE,
    QUIET_CPU_CURVE,
    QUIET_GPU_CURVE,
    CurveProfile,
    DualCurveController,
    FanCurve,
    profile_curve_points,
)


class FanCurveTests(unittest.TestCase):
    def test_balanced_curve_interpolation_and_rounding(self) -> None:
        curve = FanCurve()
        self.assertEqual(curve.interpolated_rpm(55), 2000)
        self.assertEqual(curve.interpolated_rpm(75), 3700)
        self.assertEqual(curve.requested_rpm(74.49), 3600)
        self.assertEqual(curve.requested_rpm(74.50), 3700)

    def test_curve_clamps_below_and_above_endpoints(self) -> None:
        curve = FanCurve()
        self.assertEqual(curve.target_rpm(10), 1800)
        curve.reset_hysteresis()
        self.assertEqual(curve.target_rpm(120), 5300)

    def test_curve_hysteresis_prevents_adjacent_step_bounce(self) -> None:
        curve = FanCurve([(50, 2000), (60, 3000)])
        self.assertEqual(curve.target_rpm(55.0), 2500)
        self.assertEqual(curve.target_rpm(55.6), 2500)
        self.assertEqual(curve.target_rpm(55.8), 2600)
        self.assertEqual(curve.target_rpm(55.4), 2600)
        self.assertEqual(curve.target_rpm(55.2), 2500)

    def test_curve_validation(self) -> None:
        with self.assertRaises(ValueError):
            FanCurve([(60, 3000), (50, 2400)])
        with self.assertRaises(ValueError):
            FanCurve([(50, 1700), (60, 3000)])
        with self.assertRaises(ValueError):
            FanCurve([(50, 1850), (60, 3000)])

    def test_exact_builtin_profile_points(self) -> None:
        expected = {
            CurveProfile.QUIET: (QUIET_CPU_CURVE, QUIET_GPU_CURVE),
            CurveProfile.BALANCED: (BALANCED_CPU_CURVE, BALANCED_GPU_CURVE),
            CurveProfile.PERFORMANCE: (
                PERFORMANCE_CPU_CURVE,
                PERFORMANCE_GPU_CURVE,
            ),
        }
        self.assertEqual(BUILTIN_PROFILE_CURVES, expected)
        for profile, points in expected.items():
            self.assertEqual(profile_curve_points(profile), points)

    def test_dual_curve_uses_hotter_components_request(self) -> None:
        controller = DualCurveController(BALANCED_CPU_CURVE, BALANCED_GPU_CURVE)
        hot_gpu = controller.evaluate(50, 80, now=0)
        self.assertEqual(hot_gpu.cpu_request_rpm, 1800)
        self.assertEqual(hot_gpu.gpu_request_rpm, 5100)
        self.assertEqual(hot_gpu.effective_rpm, 5100)

        controller.reset()
        hot_cpu = controller.evaluate(85, 45, now=1)
        self.assertEqual(hot_cpu.cpu_request_rpm, 4800)
        self.assertEqual(hot_cpu.gpu_request_rpm, 1800)
        self.assertEqual(hot_cpu.effective_rpm, 4800)

    def test_missing_gpu_is_not_interpreted_as_zero(self) -> None:
        controller = DualCurveController(BALANCED_CPU_CURVE, BALANCED_GPU_CURVE)
        result = controller.evaluate(70, None, now=0)
        self.assertIsNone(result.gpu_request_rpm)
        self.assertEqual(result.effective_rpm, 3200)

    def test_rise_is_immediate_and_reduction_waits_ten_seconds(self) -> None:
        controller = DualCurveController(
            BALANCED_CPU_CURVE,
            BALANCED_GPU_CURVE,
            down_hold_seconds=10,
        )
        self.assertEqual(controller.evaluate(80, 45, now=0).effective_rpm, 4200)
        self.assertEqual(controller.evaluate(85, 45, now=1).effective_rpm, 4800)
        first_low = controller.evaluate(50, 45, now=2)
        self.assertEqual(first_low.effective_rpm, 4800)
        self.assertTrue(first_low.ramp_down_pending)
        self.assertEqual(controller.evaluate(50, 45, now=11.9).effective_rpm, 4800)
        completed = controller.evaluate(50, 45, now=12.0)
        self.assertEqual(completed.effective_rpm, 1800)
        self.assertFalse(completed.ramp_down_pending)

    def test_continued_or_stronger_reduction_does_not_reset_timer(self) -> None:
        controller = DualCurveController(
            BALANCED_CPU_CURVE,
            BALANCED_GPU_CURVE,
            down_hold_seconds=10,
        )
        controller.evaluate(90, 83, now=0)
        controller.evaluate(80, 75, now=1)
        controller.evaluate(70, 65, now=5)
        result = controller.evaluate(60, 55, now=11)
        self.assertEqual(result.effective_rpm, 2200)

    def test_custom_profile_keeps_cpu_and_gpu_independent(self) -> None:
        custom_cpu = ((40, 1800), (80, 5000))
        custom_gpu = ((40, 2400), (80, 5300))
        cpu, gpu = profile_curve_points(CurveProfile.CUSTOM, custom_cpu, custom_gpu)
        self.assertEqual(cpu, custom_cpu)
        self.assertEqual(gpu, custom_gpu)


if __name__ == "__main__":
    unittest.main()
