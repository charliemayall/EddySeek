"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Sensor Z entry guard and re-exports for kinematic / gcode session helpers.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from ..kconsole import ConsoleSymbols
from .gcode_state import clear_gcode_offset_xy
from .kinematic_guard import (
    MAX_ACCEL,
    MAX_SCV,
    MCR_DEFAULT,
    KnownKinematicLimits,
    set_kinematic_limits,
)

if TYPE_CHECKING:
    from klippy.toolhead import ToolHead

_F = TypeVar("_F", bound=Callable[..., object])

_SENSOR_Z_TOLERANCE_ABOVE = 0.25
_SENSOR_Z_TOLERANCE_BELOW = 0.0  # above is ok-ish, below could crash


def assert_sensor_z(toolhead: ToolHead, sensor_z: float | None, gcmd) -> None:
    """Raise ``gcmd.error`` when machine Z is outside the ``sensor_z`` band."""
    expected = sensor_z
    if expected is None:
        return
    actual_z = float(toolhead.get_position()[2])
    lo = expected - _SENSOR_Z_TOLERANCE_BELOW
    hi = expected + _SENSOR_Z_TOLERANCE_ABOVE
    if lo <= actual_z <= hi:
        return
    raise gcmd.error(
        f"Sensor Z guard: machine Z {actual_z:.3f} mm is outside "
        f"[{lo:.3f}, {hi:.3f}] mm (sensor_z {expected:.3f}, +{_SENSOR_Z_TOLERANCE_ABOVE:.2f}/"
        f"-{_SENSOR_Z_TOLERANCE_BELOW:.2f} mm)"
        f"{ConsoleSymbols.BR}"
        f"You must be at Z within the range above to run commands"
    )


def block_for_sensor_z(fn: _F) -> _F:
    """Run ``assert_sensor_z`` before an EddySeek ``cmd_*`` method."""

    @functools.wraps(fn)
    def wrapper(self, gcmd):
        assert_sensor_z(
            self.printer.lookup_object("toolhead"),
            self._tools.sensor_z,
            gcmd,
        )
        return fn(self, gcmd)

    return wrapper  # ty:ignore[invalid-return-type]


__all__ = [
    "MAX_ACCEL",
    "MAX_SCV",
    "MCR_DEFAULT",
    "KnownKinematicLimits",
    "assert_sensor_z",
    "block_for_sensor_z",
    "clear_gcode_offset_xy",
    "set_kinematic_limits",
]
