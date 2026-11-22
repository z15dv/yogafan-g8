"""Single-GUI ownership and activation for YogaFan G8.

The elevated Safety Service is a separate process and intentionally does not use
this guard.  Only the unprivileged desktop UI owns the local activation server.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QDir, QLockFile, QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket


class ActivatableWindow(Protocol):
    def showNormal(self) -> None: ...

    def raise_(self) -> None: ...

    def activateWindow(self) -> None: ...


def default_server_name() -> str:
    """Return a stable per-Windows-user activation endpoint name."""
    identity = os.environ.get("USERDOMAIN", "") + "\\" + os.environ.get(
        "USERNAME", "current-user"
    )
    user_tag = hashlib.sha256(identity.encode("utf-8", "replace")).hexdigest()[:16]
    return f"YogaFanG8.Gui.{user_tag}"


class SingleInstanceGuard(QObject):
    """Own one GUI instance and bring it forward on subsequent launches."""

    def __init__(
        self,
        server_name: str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.log = logging.getLogger("yogafan.single_instance")
        self.server_name = server_name or default_server_name()
        lock_tag = hashlib.sha256(self.server_name.encode("utf-8")).hexdigest()[:24]
        self._lock = QLockFile(str(Path(QDir.tempPath()) / f"{lock_tag}.lock"))
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._accept_activation)
        self._window: ActivatableWindow | None = None
        self._activation_pending = False
        self._owns_server = False

    def claim_or_notify(self, timeout_ms: int = 750) -> bool:
        """Claim GUI ownership, or notify the current owner and return ``False``."""
        if not self._lock.tryLock(0):
            if not self._notify_existing(timeout_ms):
                self.log.warning(
                    "Another GUI owns %s but could not yet be notified",
                    self.server_name,
                )
            return False

        # We own the process lock, so any remaining local-server endpoint is
        # stale.  QLocalServer alone is not an exclusive guard on Windows,
        # because named pipes can have multiple server instances.
        QLocalServer.removeServer(self.server_name)
        if self._server.listen(self.server_name):
            self._owns_server = True
            return True

        # Fail closed: an uncertain endpoint must never produce another GUI that
        # competes for the exclusive Safety Service connection.
        self.log.error(
            "Could not create GUI activation endpoint %s: %s",
            self.server_name,
            self._server.errorString(),
        )
        self._lock.unlock()
        return False

    def attach_window(self, window: ActivatableWindow) -> None:
        self._window = window
        if self._activation_pending:
            self._activation_pending = False
            QTimer.singleShot(0, self._show_window)

    def close(self) -> None:
        if self._owns_server:
            self._server.close()
            self._owns_server = False
            self._lock.unlock()

    def _notify_existing(self, timeout_ms: int) -> bool:
        socket = QLocalSocket(self)
        socket.connectToServer(self.server_name)
        if not socket.waitForConnected(timeout_ms):
            socket.abort()
            socket.deleteLater()
            return False
        socket.write(b"activate\n")
        socket.waitForBytesWritten(timeout_ms)
        socket.disconnectFromServer()
        socket.deleteLater()
        return True

    def _accept_activation(self) -> None:
        accepted = False
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            accepted = True
            socket.readAll()
            socket.disconnectFromServer()
            socket.deleteLater()
        if not accepted:
            return
        if self._window is None:
            self._activation_pending = True
        else:
            self._show_window()

    def _show_window(self) -> None:
        if self._window is None:
            self._activation_pending = True
            return
        self._window.showNormal()
        self._window.raise_()
        self._window.activateWindow()

