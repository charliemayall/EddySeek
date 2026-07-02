"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

Bracket-narrowing debug plots for TernaryStrategy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..common import Axis, Position
from ._plotly import (
    go,
    make_subplots,
    pass_color,
    plotly_available,
    session_stats_annotation,
)


@dataclass(frozen=True, slots=True)
class TernaryStep:
    axis: Axis
    iteration: int
    lo: float
    hi: float
    m1: float
    m2: float
    f1: float
    f2: float


@dataclass(frozen=True, slots=True)
class TernaryPassRecord:
    pass_num: int
    result: Position
    moved: Position
    x_steps: list[TernaryStep]
    y_steps: list[TernaryStep]
    probes: list[tuple[Position, float]]


def write_ternary_session_plot(
    *,
    passes: list[TernaryPassRecord],
    search_for: Literal["min", "max"],
) -> Any | None:
    if not plotly_available() or go is None or make_subplots is None or not passes:
        return None

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=False,
        vertical_spacing=0.08,
        row_heights=[0.45, 0.275, 0.275],
        subplot_titles=(
            "Probe overview",
            "X bracket history",
            "Y bracket history",
        ),
    )

    for record in passes:
        color = pass_color(record.pass_num)
        label = f"pass {record.pass_num}"
        if record.probes:
            xs = [position.x for position, _ in record.probes]
            ys = [position.y for position, _ in record.probes]
            freqs = [freq for _, freq in record.probes]
            fig.add_trace(
                go.Scatter(
                    x=xs,
                    y=ys,
                    mode="markers+lines",
                    name=f"{label} probes",
                    line={"color": color, "width": 1},
                    marker={"size": 8, "color": color},
                    text=[f"{freq:.1f} Hz" for freq in freqs],
                    hovertemplate=(
                        f"{label}<br>x=%{{x:.4f}} y=%{{y:.4f}} %{{text}}<extra></extra>"
                    ),
                    legendgroup=label,
                ),
                row=1,
                col=1,
            )
        fig.add_trace(
            go.Scatter(
                x=[record.result.x],
                y=[record.result.y],
                mode="markers",
                name=f"{label} result",
                marker={
                    "size": 14 if record is passes[-1] else 11,
                    "symbol": "star",
                    "color": color,
                    "line": {"width": 1, "color": "white"},
                },
                legendgroup=label,
            ),
            row=1,
            col=1,
        )
        _add_bracket_history(
            fig,
            record.x_steps,
            row=2,
            color=color,
            y_base=(record.pass_num - 1)
            * (max(len(record.x_steps), len(record.y_steps), 1) + 1),
        )
        _add_bracket_history(
            fig,
            record.y_steps,
            row=3,
            color=color,
            y_base=(record.pass_num - 1)
            * (max(len(record.x_steps), len(record.y_steps), 1) + 1),
        )

    pass_lines = []
    for record in passes:
        freqs = [freq for _, freq in record.probes]
        freq_range = (
            f"freq=[{min(freqs):.0f}, {max(freqs):.0f}] Hz" if freqs else "freq=n/a"
        )
        pass_lines.append(
            f"Pass {record.pass_num}: result=({record.result.x:+.4f}, "
            f"{record.result.y:+.4f}) mm  moved=({record.moved.x:.4f}, "
            f"{record.moved.y:.4f})  {freq_range}"
        )
    final = passes[-1].result
    stats_lines = 1 + len(pass_lines) + 1
    fig.update_xaxes(title_text="X offset (mm)", row=1, col=1)
    fig.update_yaxes(title_text="Y offset (mm)", row=1, col=1)
    fig.update_xaxes(title_text="Offset (mm)", row=2, col=1)
    fig.update_xaxes(title_text="Offset (mm)", row=3, col=1)
    fig.update_yaxes(title_text="Iteration", row=2, col=1)
    fig.update_yaxes(title_text="Iteration", row=3, col=1)
    fig.update_layout(
        annotations=[
            session_stats_annotation(
                f"Ternary alignment ({len(passes)} pass"
                f"{'' if len(passes) == 1 else 'es'})  search={search_for}",
                pass_lines,
                final=f"Final: ({final.x:+.4f}, {final.y:+.4f}) mm",
            )
        ],
        title=None,
        height=max(900, 220 + 15 * stats_lines + 40 * len(passes)),
        margin={"t": stats_lines * 15 + 12 + 48, "b": 72, "l": 60, "r": 40},
        legend={"orientation": "h", "y": -0.02, "x": 0, "xanchor": "left"},
    )
    return fig


def _add_bracket_history(
    fig: Any,
    steps: list[TernaryStep],
    *,
    row: int,
    color: str,
    y_base: float,
) -> None:
    if not steps:
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
