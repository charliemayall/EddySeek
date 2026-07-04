"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Continuous sweep path planning and capture for centroid / debug_scan strategies.
"""

from .axis import sweep_axis
from .grid import sweep_grid
from .motion import (
    capture_legs,
    iter_cross_offsets,
    plan_axis_legs,
    plan_grid_legs,
    traversal_endpoints,
)

__all__ = [
    "capture_legs",
    "iter_cross_offsets",
    "plan_axis_legs",
    "plan_grid_legs",
    "sweep_axis",
    "sweep_grid",
    "traversal_endpoints",
]
