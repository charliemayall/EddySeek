"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal

from ..common import Position
from ..plotting import PlotWriter
from ..session import SeekContext, SeekReporter
from .base import SeekStrategy

logger = logging.getLogger(__name__)


def frequency_weight(
    freq: float,
    f_min: float,
    f_max: float,
    search_for: Literal["min", "max"],
) -> float:
    if search_for == "min":
        return max(f_max - freq, 0.0)
    return max(freq - f_min, 0.0)


def weighted_centroid(
    probes: list[tuple[Position, float]],
    search_for: Literal["min", "max"],
) -> Position | None:
    """Frequency-weighted XY centroid, or ``None`` when the response is flat."""
    if not probes:
        return None
    freqs = [freq for _, freq in probes]
    f_min = min(freqs)
    f_max = max(freqs)
    weights = [frequency_weight(freq, f_min, f_max, search_for) for freq in freqs]
    total_w = sum(weights)
    if total_w < 1e-9:  # prevent division by zero
        return None
    centroid_x = (
        sum(position.x * w for (position, _), w in zip(probes, weights)) / total_w
    )
    centroid_y = (
        sum(position.y * w for (position, _), w in zip(probes, weights)) / total_w
    )
    return Position(centroid_x, centroid_y)


def axis_weighted_centroid(
    coords_and_freqs: list[tuple[float, float]],
    search_for: Literal["min", "max"],
) -> float | None:
    """1-D frequency-weighted centroid on a single axis profile."""
    if not coords_and_freqs:
        return None
    freqs = [freq for _, freq in coords_and_freqs]
    f_min = min(freqs)
    f_max = max(freqs)
    weights = [frequency_weight(freq, f_min, f_max, search_for) for freq in freqs]
    total_w = sum(weights)
    if total_w < 1e-9:
        return None
    return sum(coord * w for (coord, _), w in zip(coords_and_freqs, weights)) / total_w


class CentroidStrategy(SeekStrategy):
    def __init__(self) -> None:
        self._plotter: PlotWriter | None = None

    @property
    def name(self) -> str:
        return "centroid"

    def announce_start(self, ctx: SeekContext, reporter: SeekReporter) -> None:
        cfg = ctx.config
        if cfg.save_plots:
            self._plotter = PlotWriter(Path(cfg.result_folder), ctx.session_id)
        reporter.info(
            f"EDDY_SEEK: centroid grid_step=({cfg.grid_step_x},{cfg.grid_step_y}) mm"
        )

    def on_session_end(self, ctx: SeekContext) -> str | None:
        plotter = self._plotter
        self._plotter = None
        if plotter is None:
            self._last_plot_passes = 0
            return None
        self._last_plot_passes = plotter.centroid_pass_count
        return plotter.finalize_centroid(search_for=ctx.config.search_for)

    def _step(self, ctx: SeekContext, pass_num: int, best: Position) -> Position:
        cfg = ctx.config
        shrink = 0.5 ** (pass_num - 1)
        return self._centroid_pass(
            ctx,
            pass_num,
            best,
            cfg.grid_step_x * shrink,
            cfg.grid_step_y * shrink,
        )

    def _pass_message(
        self,
        pass_num: int,
        new: Position,
        moved: Position,
        ctx: SeekContext,
    ) -> str:
        cfg = ctx.config
        shrink = 0.5 ** (pass_num - 1)
        step_x = cfg.grid_step_x * shrink
        step_y = cfg.grid_step_y * shrink
        return (
            f"EDDY_SEEK pass {pass_num}: "
            f"centroid ({new.x:+.4f}, {new.y:+.4f}) mm  "
            f"(moved {moved.x:.4f}, {moved.y:.4f})  "
            f"grid_step=({step_x:.4f}, {step_y:.4f})"
        )

    def _centroid_pass(
        self,
        ctx: SeekContext,
        pass_num: int,
        center: Position,
        step_x: float,
        step_y: float,
    ) -> Position:
        cfg = ctx.config
        probes: list[tuple[Position, float]] = []

        for dy_mul in (-1, 0, 1):
            for dx_mul in (-1, 0, 1):
                position = (center + Position(dx_mul * step_x, dy_mul * step_y)).clamp(
                    cfg.max_jog_x, cfg.max_jog_y
                )
                freq = ctx.measure_at(position)
                probes.append((position, freq))

        result = weighted_centroid(probes, cfg.search_for)
        if result is None:
            logger.warning(
                "eddy_seek: flat frequency response on centroid grid - "
                "keeping centre (%.4f, %.4f)",
                center.x,
                center.y,
            )
            if self._plotter is not None:
                self._plotter.record_centroid_pass(
                    pass_num=pass_num,
                    center=center,
                    result=center,
                    moved=Position.zero(),
                    probes=probes,
                )
            return center

        freqs = [freq for _, freq in probes]
        clamped = result.clamp(cfg.max_jog_x, cfg.max_jog_y)
        logger.debug(
            "eddy_seek: centroid pass centre=(%.4f, %.4f) -> (%.4f, %.4f) "
            "freq_range=[%.2f, %.2f] Hz",
            center.x,
            center.y,
            clamped.x,
            clamped.y,
            min(freqs),
            max(freqs),
        )
        if self._plotter is not None:
            self._plotter.record_centroid_pass(
                pass_num=pass_num,
                center=center,
                result=clamped,
                moved=(clamped - center).abs_components(),
                probes=probes,
            )
        return clamped
