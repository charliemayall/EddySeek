"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Continuous sweep motion with frequency-weighted centroid peak finding.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from typing import Any, Literal

from ..common import Axis, Offset, Phase, samples_in_box, search_box
from ..kconsole import KConsole
from ..movement.handler import MotionSample
from ..movement.leg_planner import clamped_sweep_axis
from ..optimizer import decoupled_centroid
from ..plotting._plotly import go, plotly_available
from ..plotting.primitives import (
    BoxRecord,
    MarkerRecord,
    ScatterRecord,
    StatsRecord,
    SweepCentroidTraceRecord,
    pass_color,
)
from ..plotting.registry import StrategyPlotter, register_plotter
from ..plotting.renderer import (
    add_box,
    add_marker,
    add_scatter,
    finalize_strategy_plot,
    layout_with_stats,
)
from ..session import SeekSession
from .base import SeekStrategy

logger = logging.getLogger(__name__)

_N_COARSE = 1


class SweepCentroidStrategy(SeekStrategy):
    @property
    def name(self) -> str:
        return "sweep_centroid"

    def announce_start(self, ctx: SeekSession, console: KConsole) -> None:
        cfg = ctx.config
        logger.info(
            f"eddy_seek: sweep_centroid coarse={cfg.sweep_coarse_speed / 60.0:.2f} mm/s "
            f"fine={cfg.sweep_fine_speed / 60.0:.2f} mm/s "
            f"cross_passes={cfg.sweep_cross_passes}"
        )

    def on_session_end(self, ctx: SeekSession) -> str | None:
        return finalize_strategy_plot(ctx, self.name)

    def _phase_for_pass(self, pass_num: int) -> Phase:
        return Phase.COARSE if pass_num <= _N_COARSE else Phase.FINE

    def _step(self, ctx: SeekSession, pass_num: int, best: Offset) -> Offset:
        cfg = ctx.config
        phase = self._phase_for_pass(pass_num)
        if phase is Phase.COARSE:
            shrink = 1.0
            speed = cfg.sweep_coarse_speed
        else:
            shrink = cfg.fine_shrink ** (pass_num - 2)
            speed = cfg.sweep_fine_speed

        half_x = cfg.max_jog_x * shrink
        half_y = cfg.max_jog_y * shrink

        _, samples_x = clamped_sweep_axis(
            ctx, Axis.X, best.x, half_x, best.y, speed, phase, pass_num
        )
        _, samples_y = clamped_sweep_axis(
            ctx, Axis.Y, best.y, half_y, best.x, speed, phase, pass_num
        )
        box = search_box(best, half_x, half_y, cfg.max_jog_x, cfg.max_jog_y)
        in_box_x = samples_in_box(samples_x, box)
        in_box_y = samples_in_box(samples_y, box)
        in_box = in_box_x + in_box_y

        if len(in_box) < cfg.min_sweep_samples:
            raise RuntimeError(
                f"eddy_seek: sweep_centroid pass {pass_num} collected "
                f"{len(in_box)} in-range samples "
                f"(need >= {cfg.min_sweep_samples}). "
                "Check sensor and sweep speed."
            )

        x_profile = [(sample.offset.x, sample.freq) for sample in in_box_x]
        y_profile = [(sample.offset.y, sample.freq) for sample in in_box_y]
        result_or_none = decoupled_centroid(x_profile, y_profile, cfg.search_for)
        if result_or_none is None:
            logger.warning(
                f"eddy_seek: flat frequency response on sweep pass {pass_num} - "
                f"keeping centre ({best.x:.4f}, {best.y:.4f})"
            )
            _record_sweep_centroid_pass(
                ctx, pass_num, phase, best, best, Offset.zero(), in_box, box
            )
            return best

        result = result_or_none.clamp(cfg.max_jog_x, cfg.max_jog_y)
        freqs = [freq for _, freq in x_profile + y_profile]
        logger.info(
            f"eddy_seek: sweep_centroid pass {pass_num} {phase.value} "
            f"-> ({result.x:.4f}, {result.y:.4f}) "
            f"freq_range=[{min(freqs):.2f}, {max(freqs):.2f}] Hz ({len(in_box)} samples)"
        )
        _record_sweep_centroid_pass(
            ctx,
            pass_num,
            phase,
            best,
            result,
            (result - best).abs_components(),
            in_box,
            box,
        )
        return result

    def _pass_message(
        self,
        pass_num: int,
        new: Offset,
        moved: Offset,
        ctx: SeekSession,
    ) -> str:
        phase = self._phase_for_pass(pass_num).value
        logger.info(
            f"eddy_seek: sweep_centroid pass {pass_num} ({phase}) "
            f"moved=({moved.x:.4f}, {moved.y:.4f})"
        )
        return f"Pass {pass_num} ({phase}): {new.to_delta_str()}"


def _record_sweep_centroid_pass(
    ctx: SeekSession,
    pass_num: int,
    phase: Phase,
    center: Offset,
    result: Offset,
    moved: Offset,
    samples: list[MotionSample],
    box: tuple[float, float, float, float],
) -> None:
    rec = ctx.recorder
    if not rec.active:
        return
    label = f"pass {pass_num} ({phase.value})"
    rec.record(
        ScatterRecord(
            pass_num=pass_num,
            label=f"{label} samples",
            xs=tuple(sample.offset.x for sample in samples),
            ys=tuple(sample.offset.y for sample in samples),
            freqs=tuple(sample.freq for sample in samples),
        )
    )
    x_lo, x_hi, y_lo, y_hi = box
    rec.record(BoxRecord(pass_num, x_lo, x_hi, y_lo, y_hi))
    rec.record(MarkerRecord(pass_num, f"{label} centre", center.x, center.y, "x"))
    rec.record(MarkerRecord(pass_num, f"{label} result", result.x, result.y, "star"))
    if rec.trace:
        rec.record(
            SweepCentroidTraceRecord(
                pass_num=pass_num,
                phase=phase.value,
                center_x=center.x,
                center_y=center.y,
                result_x=result.x,
                result_y=result.y,
                samples=len(samples),
            )
        )


@register_plotter("sweep_centroid")
class SweepCentroidPlotter(StrategyPlotter):
    def render(
        self,
        records: Sequence[Any],
        *,
        search_for: Literal["min", "max"],
    ) -> Any | None:
        if not plotly_available() or go is None:
            return None

        passes: dict[int, list[Any]] = defaultdict(list)
        phases: dict[int, str] = {}
        for record in records:
            pass_num = getattr(record, "pass_num", None)
            if not isinstance(pass_num, int):
                continue
            passes[pass_num].append(record)
            if isinstance(record, SweepCentroidTraceRecord):
                phases[pass_num] = record.phase

        if not passes:
            return None

        fig = go.Figure()
        pass_nums = sorted(passes)
        pass_rows: list[dict[str, str]] = []
        for pass_num in pass_nums:
            color = pass_color(pass_num)
            phase = phases.get(pass_num, "")
            group = passes[pass_num]
            for record in group:
                if isinstance(record, ScatterRecord):
                    add_scatter(fig, record, search_for, color)
                elif isinstance(record, BoxRecord):
                    add_box(fig, record, color)
                elif isinstance(record, MarkerRecord):
                    size = (
                        14
                        if pass_num == pass_nums[-1] and record.symbol == "star"
                        else 11
                    )
                    if record.symbol == "x":
                        size = 10
                    add_marker(fig, record, color, size=size)

            scatter = next(
                (record for record in group if isinstance(record, ScatterRecord)),
                None,
            )
            result = next(
                (
                    record
                    for record in group
                    if isinstance(record, MarkerRecord) and record.symbol == "star"
                ),
                None,
            )
            center = next(
                (
                    record
                    for record in group
                    if isinstance(record, MarkerRecord) and record.symbol == "x"
                ),
                None,
            )
            freqs = list(scatter.freqs) if scatter and scatter.freqs else []
            moved = Offset.zero()
            if result is not None and center is not None:
                moved = Offset(
                    result.x - center.x, result.y - center.y
                ).abs_components()
            pass_rows.append(
                {
                    "pass": str(pass_num),
                    "phase": phase,
                    "result": (
                        f"({result.x:+.4f}, {result.y:+.4f})"
                        if result is not None
                        else "n/a"
                    ),
                    "moved": f"({moved.x:.4f}, {moved.y:.4f})",
                    "samples": str(len(scatter.xs)) if scatter else "0",
                    "freq": (
                        f"[{min(freqs):.0f}, {max(freqs):.0f}]" if freqs else "n/a"
                    ),
                }
            )

        final_marker = next(
            (
                record
                for record in passes[pass_nums[-1]]
                if isinstance(record, MarkerRecord) and record.symbol == "star"
            ),
            None,
        )
        final = (
            Offset(final_marker.x, final_marker.y)
            if final_marker is not None
            else Offset.zero()
        )
        layout_with_stats(
            fig,
            StatsRecord(
                title=(
                    f"Sweep centroid ({len(pass_nums)} pass"
                    f"{'' if len(pass_nums) == 1 else 'es'})  search={search_for}"
                ),
                columns=(
                    ("pass", "Pass"),
                    ("phase", "Phase"),
                    ("result", "Result (mm)"),
                    ("moved", "Moved (mm)"),
                    ("samples", "Samples"),
                    ("freq", "Freq (Hz)"),
                ),
                rows=tuple(pass_rows),
                footer=f"Final: ({final.x:+.4f}, {final.y:+.4f}) mm",
            ),
        )
        return fig
