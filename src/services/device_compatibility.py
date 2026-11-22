"""Conservative SMBIOS identity gate for EC write access."""

from __future__ import annotations

from dataclasses import dataclass
import re
import sys
from typing import Callable, Mapping


SUPPORTED_MACHINE_TYPE = "83BU"
SUPPORTED_PRODUCT = "Yoga Pro 9 14IRP8"


@dataclass(frozen=True)
class CompatibilityResult:
    supported: bool
    machine_type: str | None
    product_name: str | None
    reason: str


def _windows_bios_values() -> Mapping[str, str]:
    if sys.platform != "win32":
        return {}
    import winreg

    path = r"HARDWARE\DESCRIPTION\System\BIOS"
    names = ("SystemProductName", "SystemSKU", "BaseBoardProduct")
    values: dict[str, str] = {}
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
        for name in names:
            try:
                value, _kind = winreg.QueryValueEx(key, name)
            except OSError:
                continue
            values[name] = str(value)
    return values


def check_device_compatibility(
    identity_reader: Callable[[], Mapping[str, str]] = _windows_bios_values,
) -> CompatibilityResult:
    try:
        values = identity_reader()
    except Exception as exc:
        return CompatibilityResult(False, None, None, f"SMBIOS identity unavailable: {exc}")
    product = values.get("SystemProductName") or values.get("BaseBoardProduct")
    combined = " ".join(str(value) for value in values.values())
    match = re.search(r"(?<![A-Z0-9])83BU(?![A-Z0-9])", combined.upper())
    if not match:
        return CompatibilityResult(
            False,
            None,
            product,
            "Unsupported or unverified machine: required Lenovo machine type 83BU",
        )
    # Machine type is the hard safety boundary. Product strings vary by BIOS
    # revision and locale, so they are displayed but are not guessed/re-written.
    return CompatibilityResult(
        True,
        SUPPORTED_MACHINE_TYPE,
        product,
        "Confirmed supported machine type 83BU",
    )
