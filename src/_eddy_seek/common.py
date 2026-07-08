"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, overload

if TYPE_CHECKING:
    from klippy.klippy import Reactor
    from klippy.toolhead import ToolHead
    from typing_extensions import Self

    from _eddy_seek.movement.handler import MotionSample

_ROUND_PRECISION = 2
CHAR_DELTA = "\u0394"
BASE_REACTOR_YIELD_S = 0.001


def yield_to_reactor(reactor: Reactor, seconds: float = BASE_REACTOR_YIELD_S) -> None:
    """Let Klipper service the MCU before blocking on plot I/O."""
    reactor.pause(  # ty: ignore[unresolved-attribute]
        reactor.monotonic() + seconds
    )


class StrEnum(str, Enum):
    """String enum for Python 3.10+ (stdlib ``enum.StrEnum`` is 3.11+)."""

    def __str__(self) -> str:
        return self.value


class Axis(StrEnum):
    X = "x"
    Y = "y"


class Phase(StrEnum):
    COARSE = "coarse"
    FINE = "fine"


class Direction(StrEnum):
    PLUS = "+"
    MINUS = "-"

    @property
    def is_reverse(self) -> bool:
        return self is Direction.MINUS


@dataclass(frozen=True, slots=True)
class _Vector2(ABC):
    """Shared XY tuple math for Offset and Position."""

    x: float
    y: float

    @property
    @abstractmethod
    def is_absolute(self) -> bool: ...

    @property
    def is_relative(self) -> bool:
        return not self.is_absolute

    @classmethod
    def zero(cls) -> Self:
        return cls(0.0, 0.0)

    @classmethod
    def from_axis(cls, axis: Axis, along: float, cross: float) -> Self:
        if axis is Axis.X:
            return cls(along, cross)
        return cls(cross, along)

    @classmethod
    def from_pair(cls, pair: Sequence[float]) -> Self:
        return cls(float(pair[0]), float(pair[1]))

    def abs_components(self) -> Offset:
        return Offset(abs(self.x), abs(self.y))

    def with_x(self, x: float) -> Self:
        return type(self)(x, self.y)

    def with_y(self, y: float) -> Self:
        return type(self)(self.x, y)

    def with_axis(self, axis: Axis, value: float) -> Self:
        if axis is Axis.X:
            return self.with_x(value)
        return self.with_y(value)

    def clamp(self, max_x: float, max_y: float) -> Self:
        return type(self)(
            max(-max_x, min(max_x, self.x)),
            max(-max_y, min(max_y, self.y)),
        )

    def distance_to(self, other: Self) -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def to_gcode(self) -> str:
        """Return X=0.000000 Y=0.000000 format for G-code."""
        return (
            f"X={round(self.x, _ROUND_PRECISION)} Y={round(self.y, _ROUND_PRECISION)}"
        )

    def to_console_str(self) -> str:
        fmt = f"+.{_ROUND_PRECISION}f"
        return f"X={format(self.x, fmt)} Y={format(self.y, fmt)} mm"

    def to_dict(self) -> dict[str, float]:
        return {
            "x": round(self.x, _ROUND_PRECISION),
            "y": round(self.y, _ROUND_PRECISION),
        }

    @property
    def seq(self) -> tuple[float, float]:
        return self.x, self.y


@dataclass(frozen=True, slots=True)
class Offset(_Vector2):
    """Represent an XY offset (mm)"""

    @property
    def is_absolute(self) -> bool:
        return False

    def __add__(self, other: Offset) -> Offset:
        if not isinstance(other, Offset):
            return NotImplemented
        return Offset(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Offset) -> Offset:
        if not isinstance(other, Offset):
            return NotImplemented
        return Offset(self.x - other.x, self.y - other.y)

    def to_delta_str(self) -> str:
        return f"{CHAR_DELTA}X={round(self.x, _ROUND_PRECISION)} {CHAR_DELTA}Y={round(self.y, _ROUND_PRECISION)}"


@dataclass(frozen=True, slots=True)
class Position(_Vector2):
    """Absolute machine XY coordinates (mm)."""

    @property
    def is_absolute(self) -> bool:
        return True

    @classmethod
    def from_toolhead(cls, toolhead: ToolHead) -> Position:
        return cls.from_pair(toolhead.get_position())

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
) -> str:
    """``YYYY-MM-DD_HH-MM-SS_{run_label}`` under ``result_folder``."""
    t = when or datetime.now()
    ts = (
        f"{t.year:04d}-{t.month:02d}-{t.day:02d}_"
        f"{t.hour:02d}-{t.minute:02d}-{t.second:02d}"
    )
    return f"{ts}_{run_label}"


def session_artifact_basename(*, suffix: str = "", ext: str = "html") -> str:
    return f"{suffix or 'session'}.{ext}"


def session_artifact_filename(
    when: datetime | None = None,
    *,
    suffix: str = "",
    run_label: str = "run",
    ext: str = "html",
) -> str:
    """``{run_dir}/{label}.{ext}`` under ``result_folder``."""
    run_dir = session_artifact_run_dir(when, run_label=run_label)
    return f"{run_dir}/{session_artifact_basename(suffix=suffix, ext=ext)}"
