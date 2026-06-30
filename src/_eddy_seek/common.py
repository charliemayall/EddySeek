"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


@dataclass
class Position:
    x: float
    y: float

    def to_gcode(self) -> str:
        """
        Return a G-code string for this position.

        Example:
        >>> Position(10.0, 20.0).to_gcode()
        'X=10.000000 Y=20.000000'
        """
        return f"X={self.x:.6f} Y={self.y:.6f}"

    @property
    def seq(self) -> tuple[float, float]:
        return self.x, self.y


class Axis(Enum):
    x = "x"
    y = "y"
