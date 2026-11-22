"""Confirmed constants and conversions for the Yoga Pro 9 Gen 8 EC."""

from __future__ import annotations

from enum import Enum

EC_COMMAND_PORT = 0x6C
EC_DATA_PORT = 0x68

STATUS_OBF = 0x01
STATUS_IBF = 0x02

LRAM_READ_COMMAND = 0x7E

FAN_COMMAND = 0xEF
SET_FAN_1 = 0x61
SET_FAN_2 = 0x62
FAN_QUERY = 0x63
READ_FAN_1 = 0x01
READ_FAN_2 = 0x02
RESTORE_AUTO = 0x03
SUCCESS_RESPONSE = 0xAC

FAN_1_TACH = 0x0406
FAN_2_TACH = 0x04FE
FCMO = 0x0420
CPU_TEMPERATURE = 0x04B0
GPU_TEMPERATURE = 0x04B4

MIN_FAN_RAW = 18
MAX_FAN_RAW = 53
MIN_FAN_RPM = 1800
MAX_FAN_RPM = 5300
FAN_STEP_RPM = 100


class ControlMode(str, Enum):
    LENOVO_AUTO = "Lenovo Auto"
    MANUAL = "Manual"
    FAN_CURVES = "Fan Curves"
    CUSTOM_CURVE = "Fan Curves"  # Compatibility alias for v1 callers.


def raw_to_rpm(raw: int) -> int:
    return int(raw) * 100


def rpm_to_raw(rpm: int) -> int:
    if rpm % FAN_STEP_RPM:
        raise ValueError("Fan target must be a 100 RPM step")
    raw = rpm // FAN_STEP_RPM
    validate_fan_raw(raw)
    return raw


def validate_fan_raw(raw: int) -> int:
    raw = int(raw)
    if not MIN_FAN_RAW <= raw <= MAX_FAN_RAW:
        raise ValueError(
            f"Fan value {raw} is outside the permitted range "
            f"{MIN_FAN_RAW}..{MAX_FAN_RAW}"
        )
    return raw


def clamp_rpm(rpm: float) -> int:
    # Fan RPM values are positive, so this gives intuitive half-up rounding
    # instead of Python's banker rounding at exact 50 RPM boundaries.
    rounded = int((float(rpm) + FAN_STEP_RPM / 2) // FAN_STEP_RPM) * FAN_STEP_RPM
    return max(MIN_FAN_RPM, min(MAX_FAN_RPM, rounded))


def normalize_target_rpm(rpm: float) -> float:
    """Normalize a requested RPM within the validated manual range."""
    clamped = clamp_rpm(rpm)
    return (clamped - MIN_FAN_RPM) / (MAX_FAN_RPM - MIN_FAN_RPM)


def normalize_actual_rpm(rpm: float) -> float:
    """Normalize tachometer telemetry without applying manual constraints."""
    return max(0.0, min(1.0, float(rpm) / MAX_FAN_RPM))


def fcmo_name(value: int) -> str:
    return {
        0: "Intelligent",
        1: "Performance",
        2: "Battery Saving",
        3: "APF (Auto Performance)",
        4: "AQT (Auto Quiet)",
    }.get(int(value), f"Unknown ({int(value)})")
