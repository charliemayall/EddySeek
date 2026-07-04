"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Debug scan grid sweep strategy: implements spatial binning and peak pick across a grid scan
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..common import Offset
from ..kconsole import KConsole
from ..optimizer import bin_frequencies, peak_bin_center
from ..plotting import PlotWriter
from ..session import SeekSession
from .base import SeekStrategy
from .sweep.grid import sweep_grid

logger = logging.getLogger(__name__)


class DebugScanStrategy(SeekStrategy):
    def __init__(self) -> None:
        self._plotter: PlotWriter | None = None

    @property
    def name(self) -> str:
        return "debug_scan"

    def announce_start(self, ctx: SeekSession, console: KConsole) -> None:
        cfg = ctx.config
        if cfg.save_plots:
            self._plotter = PlotWriter(
                Path(cfg.result_folder),
                ctx.session_id,
                write_at=ctx.artifact_write_at,
                suffix=ctx.artifact_suffix(self.name),
                run_id=ctx.run_id,
            )
        console.warn(
            "debug_scan is for diagnostic use only; "
            "use any other strategy for alignment."
        )
        logger.debug(
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
        plotter = self._plotter
        self._plotter = None
        if plotter is None:
            self._last_plot_passes = 0
            return None
        self._last_plot_passes = plotter.debug_scan_count
        return plotter.finalize_debug_scan(search_for=ctx.config.search_for)

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
            f"eddy_seek: debug_scan -> ({result.x:.4f}, {result.y:.4f}) "
            f"freq_range=[{min(freqs):.2f}, {max(freqs):.2f}] Hz ({len(samples)} samples)"
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
        new: Offset,
        moved: Offset,
        ctx: SeekSession,
    ) -> str:
        logger.debug(
            f"eddy_seek: debug_scan pass {pass_num} moved=({moved.x:.4f}, {moved.y:.4f})"
        )
        return f"Pass {pass_num}: X={new.x:+.4f} Y={new.y:+.4f} mm"
