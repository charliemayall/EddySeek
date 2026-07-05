"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Continuous sweep motion with frequency-weighted centroid peak finding.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Literal

from ..common import Offset, Phase
from ..kconsole import KConsole
from ..movement.handler import MotionSample
from ..movement.leg_planner import (
    MotionCapture,
    SweepSettings,
    axis_sweep_centroid,
)
from ..plotting._plotly import go, plotly_available
from ..plotting.primitives import (
    Bounds,
    BoxRecord,
    MarkerRecord,
    PassMove,
    ScatterRecord,
    StatsRecord,
    SweepCentroidPassRecord,
    XYCloud,
    pass_color,
)
from ..plotting.registry import StrategyPlotter, register_plotter
from ..plotting.renderer import (
    add_box,
    add_marker,
    add_scatter,
    final_result_offset,
    finalize_strategy_plot,
    layout_with_stats,
    pass_group_stats,
)
from ..session import SeekSession
from .base import SeekStrategy

logger = logging.getLogger(__name__)

_N_COARSE = 2


class SweepCentroidStrategy(SeekStrategy):
    @property
    def name(self) -> str:
        return "sweep_centroid"

    def announce_start(self, ctx: SeekSession, console: KConsole) -> None:
        cfg = ctx.config
        logger.info(
            f"eddy_seek: sweep_centroid coarse={cfg.sweep_coarse_speed / 60.0:.2f} mm/s "
            f"fine={cfg.sweep_fine_speed / 60.0:.2f} mm/s "
            f"cross_passes={cfg.cross_passes}"
        )

    def on_session_end(self, ctx: SeekSession) -> str | None:
        return finalize_strategy_plot(ctx, self.name)

    def _phase_for_pass(self, pass_num: int) -> Phase:
        return Phase.COARSE if pass_num <= _N_COARSE else Phase.FINE

    def should_check_divergence(self, ctx: SeekSession, pass_num: int) -> bool:
        return self._phase_for_pass(pass_num) is not Phase.COARSE

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

        capture = MotionCapture(ctx.motion, ctx.session_start, ctx.sync_offset)
        settings = SweepSettings.from_config(cfg)
        sweep = axis_sweep_centroid(
            capture,
            settings,
            best,
            half_x=half_x,
            half_y=half_y,
            speed_mm_min=speed,
            phase=phase,
            pass_num=pass_num,
            label=f"sweep_centroid pass {pass_num}",
            recorder=ctx.recorder,
        )
        in_box = sweep.in_box
        x_profile = sweep.x_profile
        y_profile = sweep.y_profile
        result_or_none = sweep.centroid
        box = sweep.box

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
    ctx.recorder.record_if_active(
        SweepCentroidPassRecord(
            pass_num=pass_num,
            phase=phase.value,
            move=PassMove.compute(center, result),
            bounds=Bounds.from_box(box),
            samples=XYCloud.from_samples(samples),
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

        pass_records = [
            record for record in records if isinstance(record, SweepCentroidPassRecord)
        ]
        if not pass_records:
            return None

        fig = go.Figure()
        pass_rows: list[dict[str, str]] = []
        for record in sorted(pass_records, key=lambda item: item.pass_num):
            pass_num = record.pass_num
            color = pass_color(pass_num)
            label = f"pass {pass_num} ({record.phase})"
            add_scatter(
                fig,
                ScatterRecord(
                    pass_num,
                    f"{label} samples",
                    record.samples,
                ),
                search_for,
                color,
            )
            add_box(fig, BoxRecord(pass_num, record.bounds), color)
            add_marker(
                fig,
                MarkerRecord(pass_num, f"{label} centre", record.move.center, "x"),
                color,
                size=10,
            )
            add_marker(
                fig,
                MarkerRecord(pass_num, f"{label} result", record.move.result, "star"),
                color,
                size=11,
            )
            stats = pass_group_stats([record])
            pass_rows.append(
                {
                    "pass": str(pass_num),
                    "phase": record.phase,
                    "result": stats.format_result(),
                    "moved": stats.format_moved(),
                    "samples": str(stats.sample_count),
                    "freq": stats.format_freq_range(),
                }
            )

        final = final_result_offset(pass_records)
        layout_with_stats(
            fig,
            StatsRecord(
                title=(
                    f"Sweep centroid ({len(pass_records)} pass"
                    f"{'' if len(pass_records) == 1 else 'es'})  search={search_for}"
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
