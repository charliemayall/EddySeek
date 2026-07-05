"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, Literal

from ..common import Axis, Offset
from ..kconsole import KConsole
from ..optimizer import frequency_is_better
from ..plotting._plotly import (
    go,
    header_table,
    make_subplots,
    multi_panel_layout,
    plotly_available,
)
from ..plotting.primitives import (
    MarkerRecord,
    PassMove,
    ScatterMode,
    ScatterRecord,
    TernaryPassRecord,
    TernaryStep,
    XYCloud,
    pass_color,
)
from ..plotting.registry import StrategyPlotter, register_plotter
from ..plotting.renderer import (
    add_marker,
    add_scatter,
    final_result_offset,
    finalize_strategy_plot,
    pass_group_stats,
)
from ..session import SeekSession
from .base import SeekStrategy

logger = logging.getLogger(__name__)


class TernaryStrategy(SeekStrategy):
    @property
    def name(self) -> str:
        return "ternary"

    def announce_start(self, ctx: SeekSession, console: KConsole) -> None:
        pass

    def on_session_end(self, ctx: SeekSession) -> str | None:
        return finalize_strategy_plot(ctx, self.name)

    def _step(self, ctx: SeekSession, pass_num: int, best: Offset) -> Offset:
        pass_probes: list[tuple[Offset, float]] = []
        new_x, x_steps = self._ternary_search_1d(
            ctx,
            axis=Axis.X,
            center=best.x,
            half_range=ctx.config.max_jog_x,
            fixed=best.y,
            pass_num=pass_num,
            pass_probes=pass_probes,
        )
        new_y, y_steps = self._ternary_search_1d(
            ctx,
            axis=Axis.Y,
            center=best.y,
            half_range=ctx.config.max_jog_y,
            fixed=new_x,
            pass_num=pass_num,
            pass_probes=pass_probes,
        )
        result = best.with_x(new_x).with_y(new_y)
        _record_ternary_pass(
            ctx,
            pass_num,
            best,
            result,
            (result - best).abs_components(),
            x_steps,
            y_steps,
            pass_probes,
        )
        return result

    def _pass_message(
        self,
        pass_num: int,
        new: Offset,
        moved: Offset,
        ctx: SeekSession,
    ) -> str:
        logger.info(
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
        pass_num: int,
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
            logger.info(
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


def _record_ternary_pass(
    ctx: SeekSession,
    pass_num: int,
    center: Offset,
    result: Offset,
    moved: Offset,
    x_steps: list[TernaryStep],
    y_steps: list[TernaryStep],
    probes: list[tuple[Offset, float]],
) -> None:
    ctx.recorder.record_if_active(
        TernaryPassRecord(
            pass_num=pass_num,
            move=PassMove.compute(center, result),
            probes=XYCloud.from_probes(probes, freqs=False),
            steps=tuple(x_steps + y_steps),
        )
    )


@register_plotter("ternary")
class TernaryPlotter(StrategyPlotter):
    def render(
        self,
        records: Sequence[Any],
        *,
        search_for: Literal["min", "max"],
    ) -> Any | None:
        if not plotly_available() or go is None or make_subplots is None:
            return None

        pass_records = [
            record for record in records if isinstance(record, TernaryPassRecord)
        ]
        if not pass_records:
            return None

        fig = make_subplots(
            rows=3,
            cols=1,
            shared_xaxes=False,
            vertical_spacing=0.05,
            row_heights=[0.45, 0.275, 0.275],
            subplot_titles=(
                "Probe overview",
                "X bracket history",
                "Y bracket history",
            ),
        )

        pass_nums = sorted(record.pass_num for record in pass_records)
        pass_rows: list[dict[str, str]] = []

        for record in sorted(pass_records, key=lambda item: item.pass_num):
            pass_num = record.pass_num
            color = pass_color(pass_num)
            label = f"pass {pass_num}"
            if record.probes.xs:
                add_scatter(
                    fig,
                    ScatterRecord(
                        pass_num,
                        f"{label} probes",
                        record.probes,
                        mode=ScatterMode.MARKERS_LINES,
                    ),
                    search_for,
                    color,
                    row=1,
                    col=1,
                )
            star_size = 14 if pass_num == pass_nums[-1] else 11
            add_marker(
                fig,
                MarkerRecord(pass_num, f"{label} result", record.move.result, "star"),
                color,
                size=star_size,
                row=1,
                col=1,
            )
            x_steps = [step for step in record.steps if step.axis is Axis.X]
            y_steps = [step for step in record.steps if step.axis is Axis.Y]
            y_band = max(len(x_steps), len(y_steps), 1) + 1
            _add_bracket_history(
                fig,
                x_steps,
                row=2,
                color=color,
                y_base=(pass_num - 1) * y_band,
            )
            _add_bracket_history(
                fig,
                y_steps,
                row=3,
                color=color,
                y_base=(pass_num - 1) * y_band,
            )

            stats = pass_group_stats([record])
            pass_rows.append(
                {
                    "pass": str(pass_num),
                    "result": stats.format_result(),
                    "moved": "n/a",
                    "freq": stats.format_freq_range(),
                }
            )

        final = final_result_offset(pass_records)
        fig.update_xaxes(title_text="X offset (mm)", row=1, col=1)
        fig.update_yaxes(title_text="Y offset (mm)", row=1, col=1)
        fig.update_xaxes(title_text="Offset (mm)", row=2, col=1)
        fig.update_xaxes(title_text="Offset (mm)", row=3, col=1)
        fig.update_yaxes(title_text="Iteration", row=2, col=1)
        fig.update_yaxes(title_text="Iteration", row=3, col=1)
        fig.update_layout(
            **multi_panel_layout(
                rows=3,
                cols=1,
                title=(
                    f"Ternary alignment ({len(pass_records)} pass"
                    f"{'' if len(pass_records) == 1 else 'es'})  search={search_for}"
                ),
                tables=[
                    header_table(
                        [
                            ("pass", "Pass"),
                            ("result", "Result (mm)"),
                            ("moved", "Moved (mm)"),
                            ("freq", "Freq (Hz)"),
                        ],
                        pass_rows,
                    )
                ],
                final=f"Final: ({final.x:+.4f}, {final.y:+.4f}) mm",
            ),
        )
        from ..plotting._plotly import apply_axes_theme

        apply_axes_theme(fig)
        return fig


def _add_bracket_history(
    fig: Any,
    steps: Sequence[TernaryStep],
    *,
    row: int,
    color: str,
    y_base: float,
) -> None:
    if not steps or go is None:
        return
    for step in steps:
        y = y_base + step.iteration
        fig.add_trace(
            go.Scatter(
                x=[step.lo, step.hi, step.hi, step.lo, step.lo],
                y=[y, y, y + 0.8, y + 0.8, y],
                fill="toself",
                fillcolor=_with_alpha(color, 0.15),
                line={"color": _with_alpha(color, 0.5), "width": 1},
                hoverinfo="skip",
                showlegend=False,
            ),
            row=row,
            col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=[step.m1, step.m2],
                y=[y + 0.4, y + 0.4],
                mode="markers",
                marker={"size": 7, "color": color},
                text=[f"m1 {step.f1:.1f} Hz", f"m2 {step.f2:.1f} Hz"],
                hovertemplate="%{x:.4f} %{text}<extra></extra>",
                showlegend=False,
            ),
            row=row,
            col=1,
        )


def _with_alpha(hex_color: str, alpha: float) -> str:
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"
