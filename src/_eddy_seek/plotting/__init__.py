"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Optional HTML debug plots for alignment strategies.
"""

from __future__ import annotations

from . import centroid as _centroid  # noqa: F401 - register plotter
from . import debug_scan as _debug_scan  # noqa: F401 - register plotter
from . import sweep_centroid as _sweep_centroid  # noqa: F401 - register plotter
from ._plotly import plotly_available, write_html
from .artifacts import finalize_strategy_plot, write_figure
from .recorder import SessionRecorder
from .registry import register_plotter, render_session_plot
from .renderer import PASS_COLORS, pass_color

__all__ = [
    "PASS_COLORS",
    "SessionRecorder",
    "finalize_strategy_plot",
    "pass_color",
    "plotly_available",
    "register_plotter",
    "render_session_plot",
    "write_figure",
    "write_html",
]
