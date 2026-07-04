"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..common import Axis, Offset
from ..kconsole import KConsole
from ..optimizer import frequency_is_better
from ..plotting import PlotWriter, TernaryStep
from ..session import SeekSession
from .base import SeekStrategy

logger = logging.getLogger(__name__)


class TernaryStrategy(SeekStrategy):
    def __init__(self) -> None:
        self._plotter: PlotWriter | None = None

    @property
    def name(self) -> str:
        return "ternary"

    def announce_start(self, ctx: SeekSession, console: KConsole) -> None:
        if ctx.config.save_plots:
            self._plotter = PlotWriter(
                Path(ctx.config.result_folder),
                ctx.session_id,
                write_at=ctx.artifact_write_at,
                suffix=ctx.artifact_suffix(self.name),
                run_id=ctx.run_id,
            )

    def on_session_end(self, ctx: SeekSession) -> str | None:
        plotter = self._plotter
        self._plotter = None
        if plotter is None:
            self._last_plot_passes = 0
            return None
        self._last_plot_passes = plotter.ternary_pass_count
        return plotter.finalize_ternary(search_for=ctx.config.search_for)

    def _step(self, ctx: SeekSession, pass_num: int, best: Offset) -> Offset:
        cfg = ctx.config
        pass_probes: list[tuple[Offset, float]] = []
        new_x, x_steps = self._ternary_search_1d(
            ctx,
            axis=Axis.X,
            center=best.x,
            half_range=cfg.max_jog_x,
            fixed=best.y,
            pass_probes=pass_probes,
        )
        new_y, y_steps = self._ternary_search_1d(
            ctx,
            axis=Axis.Y,
            center=best.y,
            half_range=cfg.max_jog_y,
            fixed=new_x,
            pass_probes=pass_probes,
        )
        result = best.with_x(new_x).with_y(new_y)
        if self._plotter is not None:
            self._plotter.record_ternary_pass(
                pass_num=pass_num,
                result=result,
                moved=(result - best).abs_components(),
                x_steps=x_steps,
                y_steps=y_steps,
                probes=pass_probes,
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
            f"eddy_seek: ternary pass {pass_num} moved=({moved.x:.4f}, {moved.y:.4f}) Moved: {moved.to_delta_str()}"
        )
        return f"Pass {pass_num}: {new.to_delta_str()}"

    def _ternary_search_1d(
        self,
        ctx: SeekSession,
        axis: Axis,
        center: float,
        half_range: float,
        fixed: float,
        pass_probes: list[tuple[Offset, float]],
    ) -> tuple[float, list[TernaryStep]]:
        cfg = ctx.config
        lo = max(-half_range, center - half_range)
        hi = min(half_range, center + half_range)
        steps: list[TernaryStep] = []

        for iteration in range(cfg.max_iter):
            span = hi - lo
            if span < cfg.tolerance:
                break

            m1 = lo + span / 3.0
            m2 = hi - span / 3.0

            cross_axis = Axis.Y if axis is Axis.X else Axis.X
            probe = Offset.zero().with_axis(cross_axis, fixed)
            pos_m1 = probe.with_axis(axis, m1)
            pos_m2 = probe.with_axis(axis, m2)
            f1 = ctx.measure_at(pos_m1)
            f2 = ctx.measure_at(pos_m2)
            pass_probes.append((pos_m1, f1))
            pass_probes.append((pos_m2, f2))

            better = (
                "m1" if frequency_is_better(f1, f2, ctx.config.search_for) else "m2"
            )
            logger.debug(
                f"eddy_seek: ternary {axis.value} lo={lo:.4f} hi={hi:.4f} "
                f"m1={m1:.4f}({f1:.2f} Hz) m2={m2:.4f}({f2:.2f} Hz) better={better}"
            )

            steps.append(
                TernaryStep(
                    axis=axis,
                    iteration=iteration,
                    lo=lo,
                    hi=hi,
                    m1=m1,
                    m2=m2,
                    f1=f1,
                    f2=f2,
                )
            )

            if frequency_is_better(f1, f2, ctx.config.search_for):
                hi = m2
            else:
                lo = m1

        return (lo + hi) / 2.0, steps
