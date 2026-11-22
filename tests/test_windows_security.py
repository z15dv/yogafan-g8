from __future__ import annotations

import unittest

from services.windows_security import (
    restricted_sddl,
)


class WindowsSecurityTests(unittest.TestCase):
    def test_restricted_pipe_acl_names_only_system_admins_and_user(self) -> None:
        sid = "S-1-5-21-100-200-300-1001"
        sddl = restricted_sddl(sid)
        self.assertIn("(A;;GA;;;SY)", sddl)
        self.assertIn("(A;;GA;;;BA)", sddl)
        self.assertIn(f"(A;;GA;;;{sid})", sddl)
        self.assertIn("S:(ML;;NW;;;ME)", sddl)
        self.assertNotIn(";;;WD)", sddl)
        self.assertNotIn(";;;AN)", sddl)

    def test_invalid_sid_is_rejected_before_native_api_use(self) -> None:
        with self.assertRaises(ValueError):
            restricted_sddl("Everyone")


if __name__ == "__main__":
    unittest.main()
