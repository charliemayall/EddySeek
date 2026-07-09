"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Shared movement domain types: path segments and correlated sensor samples.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..common import Offset


@dataclass(frozen=True, slots=True)
class Segment:
    """One path chord; ``capture`` marks sensor-window traverses vs connectors."""

    start: Offset
    end: Offset
    capture: bool = True

    @property
    def span_mm(self) -> float:
        return math.hypot(self.end.x - self.start.x, self.end.y - self.start.y)


@dataclass(frozen=True, slots=True)
class MotionSample:
    """One sensor reading correlated to session-relative XY."""

    offset: Offset
    freq: float
    print_time: float
