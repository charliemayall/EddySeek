"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from typing import Any, Literal

from ..common import Offset
from ..kconsole import KConsole
from ..optimizer import weighted_centroid
from ..plotting._plotly import go, plotly_available
from ..plotting.primitives import (
    MarkerRecord,
    ScatterMode,
    ScatterRecord,
    StatsRecord,
    XYCloud,
    pass_color,
)
from ..plotting.registry import StrategyPlotter, register_plotter
from ..plotting.renderer import (
    add_marker,
    add_scatter,
    finalize_strategy_plot,
    layout_with_stats,
)
from ..session import SeekSession
from .base import SeekStrategy

logger = logging.getLogger(__name__)


class CentroidStrategy(SeekStrategy):
    @property
    def name(self) -> str:
        return "centroid"

    def announce_start(self, ctx: SeekSession, console: KConsole) -> None:
        cfg = ctx.config
        logger.info(
            f"eddy_seek: centroid grid_step=({cfg.grid_step_x:.4f}, {cfg.grid_step_y:.4f}) mm"
        )

    def on_session_end(self, ctx: SeekSession) -> str | None:
        return finalize_strategy_plot(ctx, self.name)

    def _step(self, ctx: SeekSession, pass_num: int, best: Offset) -> Offset:
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
        new: Offset,
        moved: Offset,
        ctx: SeekSession,
    ) -> str:
        cfg = ctx.config
        shrink = 0.5 ** (pass_num - 1)
        step_x = cfg.grid_step_x * shrink
        step_y = cfg.grid_step_y * shrink
        logger.info(
            f"eddy_seek: centroid pass {pass_num} moved=({moved.x:.4f}, {moved.y:.4f}) "
            f"grid_step=({step_x:.4f}, {step_y:.4f})"
        )
        return f"Pass {pass_num}: {new.to_delta_str()}"

    def _centroid_pass(
        self,
        ctx: SeekSession,
        pass_num: int,
        center: Offset,
        step_x: float,
        step_y: float,
    ) -> Offset:
        cfg = ctx.config
        probes: list[tuple[Offset, float]] = []

        for dy_mul in (-1, 0, 1):
            for dx_mul in (-1, 0, 1):
                position = (center + Offset(dx_mul * step_x, dy_mul * step_y)).clamp(
                    cfg.max_jog_x, cfg.max_jog_y
                )
                freq = ctx.measure_at(position)
                probes.append((position, freq))

        result = weighted_centroid(probes, cfg.search_for)
        if result is None:
            logger.warning(
                f"eddy_seek: flat frequency response on centroid grid - "
                f"keeping centre ({center.x:.4f}, {center.y:.4f})"
            )
            _record_centroid_pass(ctx, pass_num, center, center, Offset.zero(), probes)
            return center

        freqs = [freq for _, freq in probes]
        clamped = result.clamp(cfg.max_jog_x, cfg.max_jog_y)
        logger.info(
            f"eddy_seek: centroid pass centre=({center.x:.4f}, {center.y:.4f}) "
            f"-> ({clamped.x:.4f}, {clamped.y:.4f}) "
            f"freq_range=[{min(freqs):.2f}, {max(freqs):.2f}] Hz"
        )
        _record_centroid_pass(
            ctx,
            pass_num,
            center,
            clamped,
            (clamped - center).abs_components(),
            probes,
        )
        return clamped


def _record_centroid_pass(
    ctx: SeekSession,
    pass_num: int,
    center: Offset,
    result: Offset,
    moved: Offset,
    probes: list[tuple[Offset, float]],
) -> None:
    rec = ctx.recorder
    if not rec.active:
        return
    label = f"pass {pass_num}"
    rec.record(
        ScatterRecord(
            pass_num=pass_num,
            label=f"{label} probes",
            cloud=XYCloud(
                tuple(position.x for position, _ in probes),
                tuple(position.y for position, _ in probes),
                tuple(freq for _, freq in probes),
            ),
            mode=ScatterMode.MARKERS_LINES,
        )
    )
    rec.record(MarkerRecord(pass_num, f"{label} centre", center, "x"))
    rec.record(MarkerRecord(pass_num, f"{label} result", result, "star"))


@register_plotter("centroid")
class CentroidPlotter(StrategyPlotter):
    def render(
        self,
        records: Sequence[Any],
        *,
        search_for: Literal["min", "max"],
    ) -> Any | None:
        if not plotly_available() or go is None:
            return None

        passes: dict[int, list[Any]] = defaultdict(list)
        for record in records:
            pass_num = getattr(record, "pass_num", None)
            if isinstance(pass_num, int):
                passes[pass_num].append(record)
        if not passes:
            return None

        fig = go.Figure()
        pass_nums = sorted(passes)
        pass_rows: list[dict[str, str]] = []
        for pass_num in pass_nums:
            color = pass_color(pass_num)
            group = passes[pass_num]
            for record in group:
                if isinstance(record, ScatterRecord):
                    add_scatter(fig, record, search_for, color)
                elif isinstance(record, MarkerRecord):
                    size = 11
                    if record.symbol == "x":
                        size = 10
                    if record.symbol == "star":
                        size = 14 if pass_num == pass_nums[-1] else 11
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
            freqs = list(scatter.cloud.freqs) if scatter and scatter.cloud.freqs else []
            moved = Offset.zero()
            if result is not None and center is not None:
                moved = (result.at - center.at).abs_components()
            pass_rows.append(
                {
                    "pass": str(pass_num),
                    "result": (
                        f"({result.at.x:+.4f}, {result.at.y:+.4f})"
                        if result is not None
                        else "n/a"
                    ),
                    "moved": f"({moved.x:.4f}, {moved.y:.4f})",
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
        final = final_marker.at if final_marker is not None else Offset.zero()
        layout_with_stats(
            fig,
            StatsRecord(
                title=(
                    f"Centroid alignment ({len(pass_nums)} pass"
                    f"{'' if len(pass_nums) == 1 else 'es'})  search={search_for}"
                ),
                columns=(
                    ("pass", "Pass"),
                    ("result", "Result (mm)"),
                    ("moved", "Moved (mm)"),
                    ("freq", "Freq (Hz)"),
                ),
                rows=tuple(pass_rows),
                footer=f"Final: ({final.x:+.4f}, {final.y:+.4f}) mm",
            ),
        )
        return fig
