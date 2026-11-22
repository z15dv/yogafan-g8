"""Independent watchdog core and authenticated local IPC transport.

The core is platform-neutral and fully mock-testable.  The transport uses
Windows named pipes through ``multiprocessing.connection``.  Installing the
server as an SCM service is deliberately an explicit administrator action.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from multiprocessing.connection import Client, Listener
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Callable

from control.controller import ApplicationState, FanController
from control.fan_curve import BALANCED_CPU_CURVE, BALANCED_GPU_CURVE, CurveProfile
from control.safety import ThermalProtectionPolicy
from ec.protocol import ControlMode
from services.temperature_provider import GpuTemperatureProvider
from services.windows_security import RestrictedAuthenticatedListener


DEFAULT_PIPE = r"\\.\pipe\YogaFanG8Safety"
HEARTBEAT_INTERVAL_SECONDS = 2.0
LEASE_TIMEOUT_SECONDS = 6.0


class ServiceBusyError(RuntimeError):
    pass


class ServiceAuthenticationError(RuntimeError):
    pass


class SafetyInterventionError(RuntimeError):
    pass


@dataclass(frozen=True)
class WatchdogEvent:
    timestamp: float
    event: str
    success: bool
    detail: str = ""


class SafetyServiceCore:
    """Own the controller and restore Auto when the authenticated lease dies."""

    def __init__(
        self,
        controller: FanController,
        *,
        lease_timeout: float = LEASE_TIMEOUT_SECONDS,
        thermal_poll_interval: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.controller = controller
        self.lease_timeout = float(lease_timeout)
        self._monotonic = monotonic
        self.thermal_poll_interval = float(thermal_poll_interval)
        self._lock = threading.RLock()
        self._client_id: str | None = None
        self._last_heartbeat: float | None = None
        self._started = False
        self._last_thermal_poll: float | None = None
        self.thermal_policy = ThermalProtectionPolicy()
        self.events: list[WatchdogEvent] = []
        self.log = logging.getLogger("yogafan.safety_service")

    @property
    def active_client(self) -> str | None:
        return self._client_id

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self.controller.initialize()
            self._started = True
            self._record("service_start_auto_restore", True)

    def connect(self, client_id: str) -> dict:
        with self._lock:
            self.start()
            now = self._monotonic()
            if self._client_id is not None and self._client_id != client_id:
                if not self._lease_expired(now):
                    raise ServiceBusyError("Another YogaFan G8 client owns the control lease")
                self._recover_locked("expired lease before reconnect")
            # Every new authenticated session begins from confirmed Lenovo Auto.
            self.controller.restore_auto(reason="new authenticated service session")
            self.thermal_policy.configure(True, 95, 85)
            if self.controller.thermal_latched:
                self.controller.clear_thermal_latch()
            self._client_id = str(client_id)
            self._last_heartbeat = now
            self._last_thermal_poll = now
            self._record("client_connected", True, self._client_id)
            return self.status()

    def heartbeat(self, client_id: str) -> dict:
        with self._lock:
            self._require_client(client_id)
            self._last_heartbeat = self._monotonic()
            return self.status()

    def disconnect(self, client_id: str, reason: str = "client disconnect") -> bool:
        with self._lock:
            self._require_client(client_id)
            restored = self._recover_locked(reason)
            return restored

    def tick(self) -> bool:
        """Return True only when this tick performed watchdog recovery."""
        with self._lock:
            now = self._monotonic()
            if self._client_id is None:
                return False
            if self._lease_expired(now):
                self._recover_locked("heartbeat timeout")
                return True
            if (
                self.controller.mode is not ControlMode.LENOVO_AUTO
                and (
                    self._last_thermal_poll is None
                    or now - self._last_thermal_poll >= self.thermal_poll_interval
                )
            ):
                self._last_thermal_poll = now
                try:
                    sample = self.controller.read_telemetry()
                    self._enforce_sample_locked(sample)
                except SafetyInterventionError:
                    self._client_id = None
                    self._last_heartbeat = None
                    return True
                except Exception as exc:
                    self._recover_locked(f"service telemetry failure: {exc}")
                    return True
            return False

    def dispatch(self, client_id: str, command: str, payload: dict | None = None):
        """Strict allow-list; arbitrary EC operations are intentionally absent."""
        payload = payload or {}
        with self._lock:
            self._require_client(client_id)
            self._last_heartbeat = self._monotonic()
            if command == "status":
                return self.status()
            if command == "configure_thermal_protection":
                self.thermal_policy.configure(
                    bool(payload.get("enabled", True)),
                    int(payload.get("cpu_critical_c", 95)),
                    int(payload.get("gpu_critical_c", 85)),
                )
                return self.status()
            if command == "read_telemetry":
                sample = self.controller.read_telemetry()
                self._enforce_sample_locked(sample)
                return sample
            if command == "restore_auto":
                self.controller.restore_auto(reason=str(payload.get("reason", "client")))
                return self.status()
            if command == "set_mode":
                self._enforce_sample_locked(self.controller.read_telemetry())
                self.controller.set_mode(ControlMode(payload["mode"]))
                return self.status()
            if command == "enter_manual_from_actual":
                sample = self.controller.read_telemetry()
                self._enforce_sample_locked(sample)
                return self.controller.enter_manual_from_actual(
                    sample.fan1_rpm,
                    sample.fan2_rpm,
                    bool(payload.get("linked", False)),
                )
            if command == "set_manual_targets":
                self._enforce_sample_locked(self.controller.read_telemetry())
                self.controller.set_manual_targets(
                    int(payload["fan1_rpm"]), int(payload["fan2_rpm"])
                )
                return self.status()
            if command == "set_custom_curves":
                self.controller.set_custom_curves(
                    payload["cpu_points"], payload["gpu_points"]
                )
                return self.status()
            if command == "set_profile":
                self.controller.set_profile(CurveProfile(payload["profile"]))
                return self.status()
            if command == "apply_curves":
                sample = self.controller.read_telemetry()
                self._enforce_sample_locked(sample)
                supplied_gpu = payload.get("gpu_c")
                if GpuTemperatureProvider.is_valid_ec(sample.gpu_c):
                    gpu_c = float(sample.gpu_c)
                elif supplied_gpu is None:
                    gpu_c = None
                else:
                    gpu_c = float(supplied_gpu)
                    if not 1 <= gpu_c <= 125:
                        raise ValueError("GPU temperature is outside the plausible range")
                return self.controller.apply_curves(
                    float(sample.cpu_c),
                    gpu_c,
                    immediate=bool(payload.get("immediate", False)),
                )
            if command == "thermal_protection":
                return self.controller.trigger_thermal_protection(
                    str(payload.get("reason", "thermal protection"))
                )
            if command == "clear_thermal_latch":
                self.controller.clear_thermal_latch()
                return self.status()
            raise ValueError(f"Unsupported Safety Service command: {command}")

    def status(self) -> dict:
        return {
            "mode": self.controller.mode.value,
            "state": self.controller.state.value,
            "targets": list(self.controller.target_rpm),
            "profile": self.controller.profile.value,
            "generation": self.controller.command_generation,
            "thermal_latched": self.controller.thermal_latched,
            "thermal_protection": {
                "enabled": self.thermal_policy.enabled,
                "cpu_critical_c": self.thermal_policy.cpu_critical_c,
                "gpu_critical_c": self.thermal_policy.gpu_critical_c,
            },
            "writes_blocked": self.controller.writes_blocked,
            "last_error": self.controller.last_error,
        }

    def shutdown(self, attempts: int = 3) -> bool:
        with self._lock:
            restored = False
            detail = ""
            for attempt in range(max(1, attempts)):
                try:
                    self.controller.restore_auto(
                        reason=f"service shutdown attempt {attempt + 1}",
                        preserve_state=True,
                    )
                    restored = True
                    break
                except Exception as exc:
                    detail = str(exc)
            self._client_id = None
            self._last_heartbeat = None
            self._record("service_shutdown_auto_restore", restored, detail)
            return restored

    def _lease_expired(self, now: float) -> bool:
        return (
            self._last_heartbeat is None
            or now - self._last_heartbeat > self.lease_timeout
        )

    def _require_client(self, client_id: str) -> None:
        if self._client_id != str(client_id):
            raise ServiceAuthenticationError("Client does not own the control lease")

    def _recover_locked(self, reason: str) -> bool:
        try:
            self.controller.restore_auto(reason=f"watchdog: {reason}")
        except Exception as exc:
            self._record("watchdog_auto_restore", False, f"{reason}: {exc}")
            restored = False
        else:
            self._record("watchdog_auto_restore", True, reason)
            restored = True
        self._client_id = None
        self._last_heartbeat = None
        return restored

    def _enforce_sample_locked(self, sample) -> None:
        cpu_valid = 1 <= int(sample.cpu_c) <= 125
        gpu_valid = GpuTemperatureProvider.is_valid_ec(sample.gpu_c)
        if not cpu_valid:
            restored = self._recover_locked("CPU sensor unavailable")
            raise SafetyInterventionError(
                "CPU sensor unavailable; Lenovo Auto "
                + ("confirmed" if restored else "restore failed")
            )
        critical = []
        if self.thermal_policy.enabled:
            if sample.cpu_c >= self.thermal_policy.cpu_critical_c:
                critical.append("CPU")
            if gpu_valid and sample.gpu_c >= self.thermal_policy.gpu_critical_c:
                critical.append("GPU")
        if critical:
            reason = "critical " + "/".join(critical) + " temperature"
            restored = self.controller.trigger_thermal_protection(reason)
            self._record("thermal_auto_restore", restored, reason)
            self._client_id = None
            self._last_heartbeat = None
            raise SafetyInterventionError(
                reason + ("; Lenovo Auto confirmed" if restored else "; Auto Restore failed")
            )

    def _record(self, event: str, success: bool, detail: str = "") -> None:
        item = WatchdogEvent(self._monotonic(), event, success, detail)
        self.events.append(item)
        log = self.log.info if success else self.log.error
        log("%s: %s", event, detail)


def default_authkey_path() -> Path:
    root = Path(os.environ.get("PROGRAMDATA", Path.home()))
    return root / "YogaFan G8" / "service.auth"


def load_authkey(path: Path | None = None) -> bytes:
    """Read an installer-provisioned key; never fall back to a public constant."""
    key_path = Path(path or default_authkey_path())
    data = key_path.read_bytes()
    if len(data) < 32:
        raise ServiceAuthenticationError("Safety Service key is missing or too short")
    return data


def provision_authkey(path: Path) -> bytes:
    """Create key material for an installer that subsequently applies Windows ACLs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    key = secrets.token_bytes(48)
    with path.open("xb") as stream:
        stream.write(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def load_or_provision_authkey(
    *, path: Path | None = None, provision_if_missing: bool = False
) -> bytes:
    """Load installer key material, optionally creating it for explicit setup."""
    key_path = Path(path or default_authkey_path())
    try:
        return load_authkey(key_path)
    except FileNotFoundError:
        if not provision_if_missing:
            raise
        return provision_authkey(key_path)


class SafetyServiceServer:
    def __init__(
        self,
        core: SafetyServiceCore,
        authkey: bytes,
        address: str = DEFAULT_PIPE,
        family: str = "AF_PIPE",
        listener_factory=None,
    ) -> None:
        if len(authkey) < 32:
            raise ServiceAuthenticationError("Safety Service auth key is too short")
        self.core = core
        self.authkey = bytes(authkey)
        self.address = address
        self.family = family
        self._listener_factory = listener_factory or (
            RestrictedAuthenticatedListener if family == "AF_PIPE" else Listener
        )
        self._stop = threading.Event()
        self._listener: Listener | None = None
        self._watchdog = threading.Thread(target=self._watchdog_loop, daemon=True)

    def serve_forever(self) -> None:
        self.core.start()
        try:
            self._listener = self._listener_factory(
                self.address, family=self.family, authkey=self.authkey
            )
            self._watchdog.start()
            while not self._stop.is_set():
                connection = self._listener.accept()
                threading.Thread(
                    target=self._serve_connection,
                    args=(connection,),
                    daemon=True,
                ).start()
        finally:
            self.core.shutdown()
            if self._listener is not None:
                self._listener.close()

    def stop(self) -> None:
        self._stop.set()

    def _watchdog_loop(self) -> None:
        while not self._stop.wait(0.5):
            self.core.tick()

    def _serve_connection(self, connection) -> None:
        client_id: str | None = None
        try:
            while not self._stop.is_set():
                request = connection.recv()
                if not isinstance(request, dict):
                    raise ValueError("IPC request must be a dictionary")
                command = request.get("command")
                requested_client = str(request.get("client_id", ""))
                try:
                    if command == "connect":
                        client_id = requested_client
                        result = self.core.connect(client_id)
                    elif command == "heartbeat":
                        result = self.core.heartbeat(requested_client)
                    elif command == "disconnect":
                        result = self.core.disconnect(requested_client)
                        client_id = None
                    else:
                        result = self.core.dispatch(
                            requested_client, str(command), request.get("payload")
                        )
                except Exception as exc:
                    connection.send({"ok": False, "error": str(exc)})
                else:
                    connection.send({"ok": True, "result": result})
        except (EOFError, OSError):
            pass
        finally:
            if client_id is not None and self.core.active_client == client_id:
                try:
                    self.core.disconnect(client_id, "IPC connection lost")
                except Exception:
                    self.core.log.exception("Disconnect recovery failed")
            connection.close()


class RemoteFanController:
    """FanController-compatible proxy; the service remains sole EC owner."""

    def __init__(
        self,
        authkey: bytes,
        address: str = DEFAULT_PIPE,
        *,
        client_factory=Client,
        request_timeout: float = 3.0,
        family: str = "AF_PIPE",
    ) -> None:
        self.authkey = bytes(authkey)
        self.address = address
        self.family = family
        self._client_factory = client_factory
        self.request_timeout = float(request_timeout)
        self._connection = None
        self._lock = threading.RLock()
        self.client_id = f"gui-{os.getpid()}-{secrets.token_hex(8)}"
        self.mode = ControlMode.LENOVO_AUTO
        self.target_rpm: list[int | None] = [None, None]
        self.profile = CurveProfile.BALANCED
        self.state = ApplicationState.STARTING
        self.writes_blocked = True
        self.last_error: str | None = None
        self._generation = 0
        self._thermal_latched = False
        self._custom_cpu = tuple(BALANCED_CPU_CURVE)
        self._custom_gpu = tuple(BALANCED_GPU_CURVE)

    @property
    def command_generation(self) -> int:
        return self._generation

    @property
    def thermal_latched(self) -> bool:
        return self._thermal_latched

    @property
    def custom_curves(self):
        return self._custom_cpu, self._custom_gpu

    def initialize(self) -> None:
        with self._lock:
            desired_profile = self.profile
            if self._connection is None:
                self._connection = self._client_factory(
                    self.address, family=self.family, authkey=self.authkey
                )
            self._sync(self._request("connect"))
            self._sync(
                self._request(
                    "set_custom_curves",
                    {
                        "cpu_points": list(self._custom_cpu),
                        "gpu_points": list(self._custom_gpu),
                    },
                )
            )
            self._sync(
                self._request("set_profile", {"profile": desired_profile.value})
            )
            self.writes_blocked = False

    def heartbeat(self) -> None:
        self._sync(self._request("heartbeat"))

    def read_telemetry(self):
        sample = self._request("read_telemetry")
        if self.writes_blocked:
            self.heartbeat()
        return sample

    def poll(self):
        return self.read_telemetry()

    def restore_auto(self, *, reason: str = "user", preserve_state: bool = False):
        del preserve_state
        self._sync(self._request("restore_auto", {"reason": reason}))

    def set_mode(self, mode: ControlMode) -> None:
        mode = ControlMode(mode)
        if mode is ControlMode.LENOVO_AUTO:
            self.restore_auto()
        else:
            self._sync(self._request("set_mode", {"mode": mode.value}))

    def enter_manual_from_actual(self, _fan1: int, _fan2: int, linked: bool):
        result = self._request("enter_manual_from_actual", {"linked": linked})
        self.mode = ControlMode.MANUAL
        self.target_rpm = list(result)
        return tuple(result)

    def set_manual_targets(self, fan1_rpm: int, fan2_rpm: int) -> None:
        self._sync(
            self._request(
                "set_manual_targets",
                {"fan1_rpm": fan1_rpm, "fan2_rpm": fan2_rpm},
            )
        )

    def set_custom_curves(self, cpu_points, gpu_points) -> None:
        self._custom_cpu = tuple(tuple(point) for point in cpu_points)
        self._custom_gpu = tuple(tuple(point) for point in gpu_points)
        self._sync(
            self._request(
                "set_custom_curves",
                {"cpu_points": list(cpu_points), "gpu_points": list(gpu_points)},
            )
        )

    def set_profile(self, profile: CurveProfile) -> None:
        self.profile = CurveProfile(profile)
        self._sync(self._request("set_profile", {"profile": self.profile.value}))

    def apply_curves(self, cpu_temperature, gpu_temperature, **kwargs):
        result = self._request(
            "apply_curves",
            {
                "cpu_c": cpu_temperature,
                "gpu_c": gpu_temperature,
                "now": kwargs.get("now"),
                "immediate": kwargs.get("immediate", False),
            },
        )
        self.heartbeat()
        return result

    def trigger_thermal_protection(self, reason: str) -> bool:
        result = bool(self._request("thermal_protection", {"reason": reason}))
        self.heartbeat()
        return result

    def configure_thermal_protection(
        self,
        enabled: bool,
        cpu_critical_c: int,
        gpu_critical_c: int,
    ) -> None:
        self._sync(
            self._request(
                "configure_thermal_protection",
                {
                    "enabled": bool(enabled),
                    "cpu_critical_c": int(cpu_critical_c),
                    "gpu_critical_c": int(gpu_critical_c),
                },
            )
        )

    def suspend(self) -> bool:
        self.restore_auto(reason="suspend")
        return True

    def shutdown(self) -> None:
        with self._lock:
            if self._connection is None:
                return
            try:
                self._request("disconnect")
            finally:
                self._connection.close()
                self._connection = None
                self.writes_blocked = True

    def _request(self, command: str, payload: dict | None = None):
        with self._lock:
            if self._connection is None:
                raise ConnectionError("Safety Service is not connected")
            try:
                self._connection.send(
                    {
                        "command": command,
                        "client_id": self.client_id,
                        "payload": payload or {},
                    }
                )
                if not self._connection.poll(self.request_timeout):
                    raise TimeoutError(
                        f"Safety Service request timed out: {command}"
                    )
                response = self._connection.recv()
            except (EOFError, OSError, TimeoutError):
                self._connection.close()
                self._connection = None
                self.writes_blocked = True
                raise
            if not response.get("ok"):
                self.last_error = str(response.get("error", "Safety Service error"))
                self.writes_blocked = True
                raise RuntimeError(self.last_error)
            self.last_error = None
            return response.get("result")

    def _sync(self, status: dict) -> None:
        self.mode = ControlMode(status["mode"])
        self.target_rpm = list(status["targets"])
        self.profile = CurveProfile(status["profile"])
        self._generation = int(status["generation"])
        self._thermal_latched = bool(status["thermal_latched"])
        self.writes_blocked = bool(status.get("writes_blocked", False))
        self.last_error = status.get("last_error")
        self.state = ApplicationState(status["state"])
