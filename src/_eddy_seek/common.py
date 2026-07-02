"""
# EddySeek
#
# Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import math
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from klippy.klippy import Printer


class Axis(str, Enum):
    X = "x"
    Y = "y"


class Phase(str, Enum):
    COARSE = "coarse"
    FINE = "fine"


class Direction(str, Enum):
    PLUS = "+"
    MINUS = "-"

    @property
    def reverse(self) -> bool:
        return self is Direction.MINUS


@dataclass(frozen=True, slots=True)
class Position:
    x: float
    y: float

    @classmethod
    def zero(cls) -> Position:
        return cls(0.0, 0.0)

    @classmethod
    def from_axis(cls, axis: Axis, along: float, cross: float) -> Position:
        if axis is Axis.X:
            return cls(along, cross)
        return cls(cross, along)

    @classmethod
    def from_pair(cls, pair: Sequence[float]) -> Position:
        return cls(float(pair[0]), float(pair[1]))

    @classmethod
    def from_toolhead(cls, printer: Printer) -> Position:
        pos = printer.lookup_object("toolhead").get_position()
        return cls.from_pair(pos)

    def __add__(self, other: Position) -> Position:
        return Position(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Position) -> Position:
        return Position(self.x - other.x, self.y - other.y)

    def abs_components(self) -> Position:
        return Position(abs(self.x), abs(self.y))

    def with_x(self, x: float) -> Position:
        return Position(x, self.y)

    def with_y(self, y: float) -> Position:
        return Position(self.x, y)

    def with_axis(self, axis: Axis, value: float) -> Position:
        if axis is Axis.X:
            return self.with_x(value)
        return self.with_y(value)

    def clamp(self, max_x: float, max_y: float) -> Position:
        return Position(
            max(-max_x, min(max_x, self.x)),
            max(-max_y, min(max_y, self.y)),
        )

    def distance_to(self, other: Position) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

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


def session_artifact_filename(
    session_id: str,
    when: datetime | None = None,
    *,
    suffix: str = "",
    ext: str = "html",
) -> str:
    """``HH_MM_DD_MM_YY_{id}[_{suffix}].{ext}`` under ``result_folder``."""
    t = when or datetime.now()
    sid = session_id[:8]
    base = f"{t.hour:02d}_{t.minute:02d}_{t.day:02d}_{t.month:02d}_{t.year % 100:02d}_{sid}"
    if suffix:
        return f"{base}_{suffix}.{ext}"
    return f"{base}.{ext}"
