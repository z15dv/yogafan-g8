"""Fan-control policy for YogaFan G8."""

from .controller import FanController
from .fan_curve import DEFAULT_CURVE, FanCurve

__all__ = ["DEFAULT_CURVE", "FanController", "FanCurve"]
