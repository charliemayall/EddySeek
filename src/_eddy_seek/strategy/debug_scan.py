"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Debug scan grid sweep strategy: implements spatial binning and peak pick across a grid scan
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Literal

from ..common import Offset, Position
from ..kconsole import KConsole
from ..movement.handler import MotionSample
from ..movement.leg_planner import sweep_grid
from ..optimizer import bin_frequencies, peak_bin_center
from ..plotting.debug_scan import DebugScanRecord, write_debug_scan_plot
from ..plotting.primitives import DebugScanTraceRecord, HeatmapRecord
from ..plotting.registry import StrategyPlotter, register_plotter
from ..plotting.renderer import finalize_strategy_plot
from ..session import SeekSession
from .base import SeekStrategy

logger = logging.getLogger(__name__)


class DebugScanStrategy(SeekStrategy):
    @property
    def name(self) -> str:
        return "debug_scan"

    def announce_start(self, ctx: SeekSession, console: KConsole) -> None:
        cfg = ctx.config
        console.warn(
            "debug_scan is for diagnostic use only; "
            "use any other strategy for alignment."
        )
        logger.info(
            f"eddy_seek: debug_scan tolerance={cfg.tolerance:.4f} mm "
            f"speed={cfg.sweep_coarse_speed / 60.0:.2f} mm/s"
        )

    def search(self, ctx: SeekSession, console: KConsole) -> tuple[Offset, int]:
        best = Offset.zero()
        pass_num = 1
        new = self._step(ctx, pass_num, best)
        moved = (new - best).abs_components()
        console.info(self._pass_message(pass_num, new, moved, ctx))
        return new, 1

    def on_session_end(self, ctx: SeekSession) -> str | None:
        return finalize_strategy_plot(ctx, self.name)

    def _step(self, ctx: SeekSession, pass_num: int, best: Offset) -> Offset:
        cfg = ctx.config

        samples, box = sweep_grid(
            ctx,
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
                f"eddy_seek: flat frequency response on debug_scan grid - "
                f"keeping centre ({best.x:.4f}, {best.y:.4f})"
            )
            _record_debug_scan(ctx, best, best, samples, box, z, x_centers, y_centers)
            return best

        result = peak.clamp(cfg.max_jog_x, cfg.max_jog_y)
        freqs = [sample.freq for sample in samples]
        logger.info(
            f"eddy_seek: debug_scan -> ({result.x:.4f}, {result.y:.4f}) "
            f"freq_range=[{min(freqs):.2f}, {max(freqs):.2f}] Hz ({len(samples)} samples)"
        )
        _record_debug_scan(ctx, best, result, samples, box, z, x_centers, y_centers)
        return result

    def _pass_message(
        self,
        pass_num: int,
        new: Offset,
        moved: Offset,
        ctx: SeekSession,
    ) -> str:
        logger.info(
            f"eddy_seek: debug_scan pass {pass_num} moved=({moved.x:.4f}, {moved.y:.4f})"
        )
        return f"Pass {pass_num}: {new.to_delta_str()}"


def _record_debug_scan(
    ctx: SeekSession,
    center: Offset,
    result: Offset,
    samples: list[MotionSample],
    box: tuple[float, float, float, float],
    z: list[list[float | None]],
    x_centers: list[float],
    y_centers: list[float],
) -> None:
    rec = ctx.recorder
    if not rec.active:
        return
    x_lo, x_hi, y_lo, y_hi = box
    rec.record(
        HeatmapRecord(
            center=center,
            result=result,
            lo=Position(x_lo, y_lo),
            hi=Position(x_hi, y_hi),
            z=tuple(tuple(row) for row in z),
            x_centers=tuple(x_centers),
            y_centers=tuple(y_centers),
            sample_xs=tuple(sample.offset.x for sample in samples),
            sample_ys=tuple(sample.offset.y for sample in samples),
            sample_freqs=tuple(sample.freq for sample in samples),
        )
    )
    if rec.trace:
        rec.record(
            DebugScanTraceRecord(
                center=center,
                result=result,
                samples=len(samples),
            )
        )


@register_plotter("debug_scan")
class DebugScanPlotter(StrategyPlotter):
    def render(
        self,
        records: Sequence[Any],
        *,
        search_for: Literal["min", "max"],
    ) -> Any | None:
        heatmap = next(
            (record for record in records if isinstance(record, HeatmapRecord)),
            None,
        )
        if heatmap is None:
            return None
        samples = [
            MotionSample(Offset(x, y), freq, 0.0)
            for x, y, freq in zip(
                heatmap.sample_xs,
                heatmap.sample_ys,
                heatmap.sample_freqs,
                strict=True,
            )
        ]
        record = DebugScanRecord(
            center=heatmap.center,
            result=heatmap.result,
            samples=samples,
            box=(heatmap.lo.x, heatmap.hi.x, heatmap.lo.y, heatmap.hi.y),
            z=[list(row) for row in heatmap.z],
            x_centers=list(heatmap.x_centers),
            y_centers=list(heatmap.y_centers),
        )
        return write_debug_scan_plot(record=record, search_for=search_for)
