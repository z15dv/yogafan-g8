from __future__ import annotations

import unittest

from services.device_compatibility import check_device_compatibility


class DeviceCompatibilityTests(unittest.TestCase):
    def test_exact_machine_type_is_supported(self) -> None:
        result = check_device_compatibility(
            lambda: {
                "SystemProductName": "Yoga Pro 9 14IRP8",
                "SystemSKU": "LENOVO_MT_83BU_BU_idea_FM_Yoga Pro 9 14IRP8",
            }
        )
        self.assertTrue(result.supported)
        self.assertEqual(result.machine_type, "83BU")

    def test_other_generations_are_rejected(self) -> None:
        for sku in ("83DN", "83BUX", "Yoga Pro 9 Gen 9"):
            with self.subTest(sku=sku):
                self.assertFalse(
                    check_device_compatibility(lambda sku=sku: {"SystemSKU": sku}).supported
                )

    def test_unreadable_identity_fails_closed(self) -> None:
        def broken():
            raise OSError("denied")

        result = check_device_compatibility(broken)
        self.assertFalse(result.supported)
        self.assertIn("unavailable", result.reason)


if __name__ == "__main__":
    unittest.main()
