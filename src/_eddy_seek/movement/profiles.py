"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Axis profile projection for sweep samples.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..common import Axis
from .types import MotionSample


def axis_profile(
    samples: Sequence[MotionSample],
    axis: Axis,
    lo: float | None = None,
    hi: float | None = None,
) -> list[tuple[float, float]]:
    """Project samples onto one axis, optionally clipping to ``[lo, hi]``."""
    if axis is Axis.X:
        points = [(s.offset.x, s.freq) for s in samples]
    else:
        points = [(s.offset.y, s.freq) for s in samples]
    if lo is not None and hi is not None:
        if lo > hi:
            lo, hi = hi, lo
        points = [(coord, freq) for coord, freq in points if lo <= coord <= hi]
    return points
