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


_PLOT_PANEL = 480
_MARGIN_LEFT = 80
_MARGIN_RIGHT = 110  # Hz colorbar sits just outside the axes
_STATS_LINE_HEIGHT = 15
_LEGEND_BAND = 36


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
        "colorbar": {
            "title": "Hz",
            "x": 1.02,
            "xanchor": "left",
            "len": 0.75,
            "thickness": 14,
        },
    }


def session_stats_title(strategy: str, pass_lines: list[str], *, final: str) -> str:
    stats = "<br>".join(pass_lines)
    return f"{strategy}<br><sup>{stats}<br>{final}</sup>"


def session_stats_annotation(
    strategy: str, pass_lines: list[str], *, final: str
) -> dict[str, Any]:
    """Paper-space stats block above the axes (avoids title overlapping the plot)."""
    stats = "<br>".join(pass_lines)
    return {
        "text": f"{strategy}<br><sup>{stats}<br>{final}</sup>",
        "xref": "paper",
        "yref": "paper",
        "x": 0,
        "y": 1,
        "xanchor": "left",
        "yanchor": "top",
        "showarrow": False,
        "align": "left",
        "font": {"size": 11},
    }


def square_xy_plot_layout(*, stats_lines: int) -> dict[str, Any]:
    """Fixed-size layout so the plot panel is square (1 mm = 1 mm on screen)."""
    stats_block = stats_lines * _STATS_LINE_HEIGHT + 12
    top = stats_block + 8
    bottom = 72 + _LEGEND_BAND
    plot = _PLOT_PANEL
    left, right = _MARGIN_LEFT, _MARGIN_RIGHT
    return {
        "width": plot + left + right,
        "height": plot + top + bottom,
        "margin": {"l": left, "r": right, "t": top, "b": bottom, "pad": 0},
        "yaxis": {"scaleanchor": "x", "scaleratio": 1},
        "autosize": False,
        "legend": {"orientation": "h", "y": -0.15, "x": 0, "xanchor": "left"},
        "title": None,
    }


def xy_session_layout(
    strategy: str, pass_lines: list[str], *, final: str
) -> dict[str, Any]:
    """Square XY plot layout with session stats in the top margin."""
    stats_lines = 1 + len(pass_lines) + 1
    layout = square_xy_plot_layout(stats_lines=stats_lines)
    layout["annotations"] = [
        session_stats_annotation(strategy, pass_lines, final=final)
    ]
    return layout


def write_html(path: str | Path, fig: Any) -> bool:
    if not plotly_available():
        return False
    if go is None:
        return False
    try:
        fig.write_html(path, include_plotlyjs="cdn", config={"responsive": False})
        return True
    except OSError as exc:
        logger.warning("eddy_seek: failed to write plot to %s: %s", path, exc)
        return False
