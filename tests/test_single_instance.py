from __future__ import annotations

import os
import unittest
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtNetwork import QLocalServer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from services.single_instance import SingleInstanceGuard, default_server_name


APP = QApplication.instance() or QApplication([])


class FakeWindow:
    def __init__(self) -> None:
        self.shown = 0
        self.raised = 0
        self.activated = 0

    def showNormal(self) -> None:
        self.shown += 1

    def raise_(self) -> None:
        self.raised += 1

    def activateWindow(self) -> None:
        self.activated += 1


class SingleInstanceTests(unittest.TestCase):
    def test_server_name_is_stable_and_does_not_expose_username(self) -> None:
        first = default_server_name()
        second = default_server_name()
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("YogaFanG8.Gui."))
        username = os.environ.get("USERNAME", "")
        if username:
            self.assertNotIn(username.lower(), first.lower())

    def test_second_launch_activates_existing_window_and_exits(self) -> None:
        name = f"YogaFanG8.Test.{uuid.uuid4().hex}"
        owner = SingleInstanceGuard(name)
        second = SingleInstanceGuard(name)
        window = FakeWindow()
        try:
            self.assertTrue(owner.claim_or_notify())
            owner.attach_window(window)
            self.assertFalse(second.claim_or_notify())
            QTest.qWait(50)
            self.assertEqual(window.shown, 1)
            self.assertEqual(window.raised, 1)
            self.assertEqual(window.activated, 1)
        finally:
            second.close()
            owner.close()
            QLocalServer.removeServer(name)

    def test_activation_before_window_attachment_is_delivered_once(self) -> None:
        name = f"YogaFanG8.Test.{uuid.uuid4().hex}"
        owner = SingleInstanceGuard(name)
        second = SingleInstanceGuard(name)
        window = FakeWindow()
        try:
            self.assertTrue(owner.claim_or_notify())
            self.assertFalse(second.claim_or_notify())
            QTest.qWait(30)
            owner.attach_window(window)
            QTest.qWait(30)
            self.assertEqual(window.shown, 1)
        finally:
            second.close()
            owner.close()
            QLocalServer.removeServer(name)


if __name__ == "__main__":
    unittest.main()
