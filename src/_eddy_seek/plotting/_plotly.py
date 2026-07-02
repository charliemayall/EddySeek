"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

Optional Plotly import and HTML export helper.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except ImportError:
    go = None  # type: ignore[assignment,misc]
    make_subplots = None  # type: ignore[assignment,misc]


PASS_COLORS = (
    "#636EFA",
    "#EF553B",
    "#00CC96",
    "#AB63FA",
    "#FFA15A",
    "#19D3F3",
    "#FF6692",
    "#B6E880",
    "#FF97FF",
    "#FECB52",
)


def plotly_available() -> bool:
    return go is not None and make_subplots is not None


def pass_color(pass_num: int) -> str:
    return PASS_COLORS[(pass_num - 1) % len(PASS_COLORS)]


def freq_marker(
    freqs: list[float],
    search_for: Literal["min", "max"],
    *,
    size: int = 5,
    opacity: float = 0.75,
) -> dict[str, Any]:
    """Scatter marker dict with Viridis frequency colorscale."""
    return {
        "size": size,
        "color": freqs,
        "colorscale": "Viridis",
        "reversescale": search_for == "min",
        "opacity": opacity,
        "colorbar": {"title": "Hz"},
    }


def session_stats_title(strategy: str, pass_lines: list[str], *, final: str) -> str:
    stats = "<br>".join(pass_lines)
    return f"{strategy}<br><sup>{stats}<br>{final}</sup>"


def square_xy_plot_layout(*, title_lines: int) -> dict[str, Any]:
    """Fixed-size layout so the plot panel is square (1 mm = 1 mm, drag-zoom tracks cursor)."""
    plot = 480
    left, right, bottom = 80, 100, 80  # right margin reserves Hz colorbar space
    top = max(120, 80 + 18 * title_lines)
    return {
        "width": plot + left + right,
        "height": plot + top + bottom,
        "margin": {"l": left, "r": right, "t": top, "b": bottom},
        "yaxis": {"scaleanchor": "x", "scaleratio": 1},
        "autosize": False,
    }


def write_html(path: str | Path, fig: Any) -> bool:
    if not plotly_available():
        return False
    if go is None:
        return False
    try:
        fig.write_html(path, include_plotlyjs="cdn")
        return True
    except OSError as exc:
        logger.warning("eddy_seek: failed to write plot to %s: %s", path, exc)
        return False
