"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

Debug scan grid sweep strategy: spatial binning and peak pick.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Literal, cast

from ..common import Position
from ..continuous_motion import ContinuousMotionHandler, MotionSample
from ..plotting import PlotWriter
from ..session import SeekContext, SeekReporter, SweepContext
from .base import SeekStrategy
from .centroid import frequency_weight
from .sweep.grid import sweep_grid

logger = logging.getLogger(__name__)


def bin_frequencies(
    samples: list[MotionSample],
    box: tuple[float, float, float, float],
    tolerance: float,
    center: Position,
    search_for: Literal["min", "max"],
) -> tuple[list[list[float | None]], list[float], list[float]]:
    """Return ``(z[ny][nx] mean weight or None, x_centers, y_centers)``."""
    x_lo, x_hi, y_lo, y_hi = box
    if tolerance <= 0.0:
        raise ValueError("tolerance must be positive")
    n_x_min = math.ceil((x_lo - center.x) / tolerance - 0.5)
    n_x_max = math.floor((x_hi - center.x) / tolerance + 0.5)
    n_y_min = math.ceil((y_lo - center.y) / tolerance - 0.5)
    n_y_max = math.floor((y_hi - center.y) / tolerance + 0.5)
    x_centers = [center.x + index * tolerance for index in range(n_x_min, n_x_max + 1)]
    y_centers = [center.y + index * tolerance for index in range(n_y_min, n_y_max + 1)]
    nx = len(x_centers)
    ny = len(y_centers)

    in_box_freqs = [
        sample.freq
        for sample in samples
        if x_lo <= sample.offset.x <= x_hi and y_lo <= sample.offset.y <= y_hi
    ]
    if not in_box_freqs:
        z = [[None] * nx for _ in range(ny)]
        return z, x_centers, y_centers
    f_min = min(in_box_freqs)
    f_max = max(in_box_freqs)

    sums = [[0.0] * nx for _ in range(ny)]
    counts = [[0] * nx for _ in range(ny)]
    for sample in samples:
        x = sample.offset.x
        y = sample.offset.y
        if not (x_lo <= x <= x_hi and y_lo <= y <= y_hi):
            continue
        ix = math.floor((x - center.x) / tolerance + 0.5) - n_x_min
        iy = math.floor((y - center.y) / tolerance + 0.5) - n_y_min
        if not (0 <= ix < nx and 0 <= iy < ny):
            continue
        weight = frequency_weight(sample.freq, f_min, f_max, search_for)
        sums[iy][ix] += weight
        counts[iy][ix] += 1

    z = [
        [sums[iy][ix] / counts[iy][ix] if counts[iy][ix] else None for ix in range(nx)]
        for iy in range(ny)
    ]
    return z, x_centers, y_centers


def peak_bin_center(
    z: list[list[float | None]],
    x_centers: list[float],
    y_centers: list[float],
) -> Position | None:
    """Bin with highest mean weight. Skip empty bins and flat response."""
    best_value: float | None = None
    best_ix: int | None = None
    best_iy: int | None = None
    for iy, row in enumerate(z):
        for ix, value in enumerate(row):
            if value is None:
                continue
            if best_value is None or value > best_value:
                best_value, best_ix, best_iy = value, ix, iy
    if best_ix is None or best_iy is None or best_value < 1e-9:
        return None
    return Position(x_centers[best_ix], y_centers[best_iy])


def _assert_binning() -> None:
    tolerance = 0.1
    box = (-0.5, 0.5, -0.5, 0.5)
    peak_x, peak_y = 0.05, -0.05
    samples = [
        MotionSample(Position(peak_x, peak_y), 100.0, 0.0),
        MotionSample(Position(peak_x + 0.01, peak_y), 100.0, 0.1),
        MotionSample(Position(-0.2, 0.2), 10.0, 0.2),
    ]
    center = Position.zero()
    z, x_centers, y_centers = bin_frequencies(samples, box, tolerance, center, "max")
    peak = peak_bin_center(z, x_centers, y_centers)
    assert any(abs(x) <= tolerance / 2 for x in x_centers)
    assert any(abs(y) <= tolerance / 2 for y in y_centers)
    assert peak is not None
    assert abs(peak.x - peak_x) <= tolerance
    assert abs(peak.y - peak_y) <= tolerance
    z_min, _, _ = bin_frequencies(samples, box, tolerance, center, "min")
    low = peak_bin_center(z_min, x_centers, y_centers)
    assert low is not None
    assert low.x < peak.x or low.y > peak.y
    assert peak_bin_center([[]], [], []) is None


_assert_binning()


class DebugScanStrategy(SeekStrategy):
    def __init__(self) -> None:
        self._motion_handler: ContinuousMotionHandler | None = None
        self._plotter: PlotWriter | None = None

    @property
    def name(self) -> str:
        return "debug_scan"

    def announce_start(self, ctx: SeekContext, reporter: SeekReporter) -> None:
        sweep_ctx = cast(SweepContext, ctx)
        cfg = sweep_ctx.config
        self._motion_handler = ContinuousMotionHandler(
            sweep_ctx.host.printer, sweep_ctx.host.add_sensor_client
        )
        if cfg.save_plots:
            self._plotter = PlotWriter(Path(cfg.result_folder), ctx.session_id)
        reporter.info(
            "EDDY_SEEK: WARNING — debug_scan is for diagnostic use only; "
            "use sweep_centroid for production alignment."
        )
        reporter.info(
            f"EDDY_SEEK: debug_scan tolerance={cfg.tolerance} mm  "
            f"speed={cfg.sweep_coarse_speed} mm/s"
        )

    def search(self, ctx: SeekContext, reporter: SeekReporter) -> tuple[Position, int]:
        best = Position.zero()
        pass_num = 1
        new = self._step(ctx, pass_num, best)
        moved = (new - best).abs_components()
        reporter.info(self._pass_message(pass_num, new, moved, ctx))
        reporter.info("EDDY_SEEK: converged after 1 pass(es).")
        return new, 1

    def on_session_end(self, ctx: SeekContext) -> str | None:
        plotter = self._plotter
        self._plotter = None
        if self._motion_handler is not None:
            self._motion_handler.close()
            self._motion_handler = None
        if plotter is None:
            self._last_plot_passes = 0
            return None
        self._last_plot_passes = plotter.debug_scan_count
        return plotter.finalize_debug_scan(search_for=ctx.config.search_for)

    def _step(self, ctx: SeekContext, pass_num: int, best: Position) -> Position:
        sweep_ctx = cast(SweepContext, ctx)
        cfg = sweep_ctx.config
        if self._motion_handler is None:
            raise RuntimeError("eddy_seek: continuous motion handler not started")

        samples, box = sweep_grid(
            sweep_ctx,
            self._motion_handler,
            best,
            cfg.sweep_coarse_speed,
            cfg.tolerance,
        )
        if len(samples) < cfg.min_sweep_samples:
            raise RuntimeError(
                f"eddy_seek: debug_scan collected {len(samples)} in-range samples "
                f"(need >= {cfg.min_sweep_samples}). "
                "Check sensor and sweep speed."
            )

        z, x_centers, y_centers = bin_frequencies(
            samples, box, cfg.tolerance, best, cfg.search_for
        )
        peak = peak_bin_center(z, x_centers, y_centers)
        if peak is None:
            logger.warning(
                "eddy_seek: flat frequency response on debug_scan grid - "
                "keeping centre (%.4f, %.4f)",
                best.x,
                best.y,
            )
            if self._plotter is not None:
                self._plotter.record_debug_scan(
                    center=best,
                    result=best,
                    samples=samples,
                    box=box,
                    z=z,
                    x_centers=x_centers,
                    y_centers=y_centers,
                )
            return best

        result = peak.clamp(cfg.max_jog_x, cfg.max_jog_y)
        freqs = [sample.freq for sample in samples]
        logger.debug(
            "eddy_seek: debug_scan -> (%.4f, %.4f) freq_range=[%.2f, %.2f] Hz (%d samples)",
            result.x,
            result.y,
            min(freqs),
            max(freqs),
            len(samples),
        )
        ctx.append_trace(
            {
                "type": "debug_scan",
                "centre": {"x": best.x, "y": best.y},
                "result": {"x": result.x, "y": result.y},
                "samples": len(samples),
            }
        )
        if self._plotter is not None:
            self._plotter.record_debug_scan(
                center=best,
                result=result,
                samples=samples,
                box=box,
                z=z,
                x_centers=x_centers,
                y_centers=y_centers,
            )
        return result

    def _pass_message(
        self,
        pass_num: int,
        new: Position,
        moved: Position,
        ctx: SeekContext,
    ) -> str:
        return f"EDDY_SEEK pass {pass_num}: debug_scan ({new.x:+.4f}, {new.y:+.4f}) mm"
