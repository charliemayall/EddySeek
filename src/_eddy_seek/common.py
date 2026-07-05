"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    from klippy.klippy import Printer

    from _eddy_seek.movement.handler import MotionSample

_ROUND_PRECISION = 4
CHAR_DELTA = "\u0394"


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
class Offset:
    """Represent an XY offset (mm)"""

    x: float
    y: float

    @classmethod
    def zero(cls) -> Offset:
        return cls(0.0, 0.0)

    @classmethod
    def from_axis(cls, axis: Axis, along: float, cross: float) -> Offset:
        if axis is Axis.X:
            return cls(along, cross)
        return cls(cross, along)

    @classmethod
    def from_pair(cls, pair: Sequence[float]) -> Offset:
        return cls(float(pair[0]), float(pair[1]))

    def __add__(self, other: Offset) -> Offset:
        if not isinstance(other, Offset):
            return NotImplemented
        return Offset(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Offset) -> Offset:
        if not isinstance(other, Offset):
            return NotImplemented
        return Offset(self.x - other.x, self.y - other.y)

    def abs_components(self) -> Offset:
        return Offset(abs(self.x), abs(self.y))

    def with_x(self, x: float) -> Offset:
        return Offset(x, self.y)

    def with_y(self, y: float) -> Offset:
        return Offset(self.x, y)

    def with_axis(self, axis: Axis, value: float) -> Offset:
        if axis is Axis.X:
            return self.with_x(value)
        return self.with_y(value)

    def clamp(self, max_x: float, max_y: float) -> Offset:
        return Offset(
            max(-max_x, min(max_x, self.x)),
            max(-max_y, min(max_y, self.y)),
        )

    def distance_to(self, other: Offset) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def to_gcode(self) -> str:
        return (
            f"X={round(self.x, _ROUND_PRECISION)} Y={round(self.y, _ROUND_PRECISION)}"
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "x": round(self.x, _ROUND_PRECISION),
            "y": round(self.y, _ROUND_PRECISION),
        }

    def to_delta_str(self) -> str:
        return f"{CHAR_DELTA}X={round(self.x, _ROUND_PRECISION)} {CHAR_DELTA}Y={round(self.y, _ROUND_PRECISION)}"

    @property
    def seq(self) -> tuple[float, float]:
        return self.x, self.y


@dataclass(frozen=True, slots=True)
class Position:
    """Absolute machine XY coordinates (mm)."""

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

    @overload
    def __sub__(self, other: Position) -> Offset: ...

    @overload
    def __sub__(self, other: Offset) -> Position: ...

    def __add__(self, other: Offset) -> Position:
        if not isinstance(other, Offset):
            return NotImplemented
        return Position(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Position | Offset) -> Offset | Position:
        if isinstance(other, Position):
            return Offset(self.x - other.x, self.y - other.y)
        if isinstance(other, Offset):
            return Position(self.x - other.x, self.y - other.y)
        return NotImplemented

    def abs_components(self) -> Offset:
        return Offset(abs(self.x), abs(self.y))

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
        'X=10.0000 Y=20.0000'
        """
        return (
            f"X={round(self.x, _ROUND_PRECISION)} Y={round(self.y, _ROUND_PRECISION)}"
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "x": round(self.x, _ROUND_PRECISION),
            "y": round(self.y, _ROUND_PRECISION),
        }

    @property
    def seq(self) -> tuple[float, float]:
        return self.x, self.y


def search_box(
    center: Offset,
    half_x: float,
    half_y: float,
    max_jog_x: float,
    max_jog_y: float,
) -> tuple[float, float, float, float]:
    x_lo = max(-max_jog_x, center.x - half_x)
    x_hi = min(max_jog_x, center.x + half_x)
    y_lo = max(-max_jog_y, center.y - half_y)
    y_hi = min(max_jog_y, center.y + half_y)
    return x_lo, x_hi, y_lo, y_hi


def samples_in_box(
    samples: list[MotionSample],
    box: tuple[float, float, float, float],
) -> list[MotionSample]:
    x_lo, x_hi, y_lo, y_hi = box
    return [
        sample
        for sample in samples
        if x_lo <= sample.offset.x <= x_hi and y_lo <= sample.offset.y <= y_hi
    ]


def session_artifact_run_dir(
    when: datetime | None = None,
    *,
    run_label: str = "run",
    run_id: str | None = None,
) -> str:
    """``YYYY-MM-DD_HH-MM-SS_{run_label}_{run_id}`` under ``result_folder``."""
    t = when or datetime.now()
    ts = (
        f"{t.year:04d}-{t.month:02d}-{t.day:02d}_"
        f"{t.hour:02d}-{t.minute:02d}-{t.second:02d}"
    )
    rid = (run_id or "")[:8]
    return f"{ts}_{run_label}_{rid}" if rid else f"{ts}_{run_label}"


def session_artifact_basename(*, suffix: str = "", ext: str = "html") -> str:
    return f"{suffix or 'session'}.{ext}"


def session_artifact_filename(
    when: datetime | None = None,
    *,
    suffix: str = "",
    run_label: str = "run",
    run_id: str | None = None,
    ext: str = "html",
) -> str:
    """``{run_dir}/{label}.{ext}`` under ``result_folder``."""
    run_dir = session_artifact_run_dir(when, run_label=run_label, run_id=run_id)
    return f"{run_dir}/{session_artifact_basename(suffix=suffix, ext=ext)}"
