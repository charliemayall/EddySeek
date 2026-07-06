"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Centroid strategy session plot.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from ._plotly import go, plotly_available
from .primitives import CentroidPassRecord
from .registry import StrategyPlotter, register_plotter
from .renderer import render_pass_xy_figure


def render_centroid_figure(
    records: Sequence[Any],
    *,
    search_for: Literal["min", "max"],
) -> Any | None:
    if not plotly_available() or go is None:
        return None

    pass_records = [
        record for record in records if isinstance(record, CentroidPassRecord)
    ]
    if not pass_records:
        return None

    return render_pass_xy_figure(
        pass_records,
        search_for=search_for,
        draw_bounds=False,
        extra_columns=(),
        title_prefix="Centroid alignment",
    )


@register_plotter("centroid")
class CentroidPlotter(StrategyPlotter):
    def render(
        self,
        records: Sequence[Any],
        *,
        search_for: Literal["min", "max"],
    ) -> Any | None:
        return render_centroid_figure(records, search_for=search_for)
