"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

XY search algorithms for eddy_seek.
"""

from __future__ import annotations

from .base import SeekStrategy
from .centroid import CentroidStrategy
from .debug_scan import DebugScanStrategy
from .sweep_centroid import SweepCentroidStrategy
from .ternary import TernaryStrategy

_STRATEGIES: dict[str, type[SeekStrategy]] = {
    "ternary": TernaryStrategy,
    "centroid": CentroidStrategy,
    "sweep_centroid": SweepCentroidStrategy,
    "debug_scan": DebugScanStrategy,
}


def strategy_for(name: str) -> SeekStrategy:
    try:
        return _STRATEGIES[name]()
    except KeyError as exc:
        raise ValueError(
            f"eddy_seek: unknown strategy {name!r} "
            f"(known: {', '.join(sorted(_STRATEGIES))})"
        ) from exc


__all__ = [
    "CentroidStrategy",
    "DebugScanStrategy",
    "SeekStrategy",
    "SweepCentroidStrategy",
    "TernaryStrategy",
    "strategy_for",
]
