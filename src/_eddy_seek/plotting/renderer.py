"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Shared Plotly builders for session record primitives.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from ..common import Offset
from ._plotly import (
    apply_axes_theme,
    freq_marker,
    go,
    header_table,
    marker_outline,
    single_xy_layout,
)
from .primitives import (
    Bounds,
    CentroidPassRecord,
    SweepCentroidPassRecord,
    XYCloud,
    _Record,
    pass_color,
    record_pass_num,
)


class ScatterMode(str, Enum):
    MARKERS = "markers"
    MARKERS_LINES = "markers+lines"


@dataclass(frozen=True, slots=True)
class ScatterRecord:
    pass_num: int
    label: str
    cloud: XYCloud
    mode: ScatterMode = ScatterMode.MARKERS


@dataclass(frozen=True, slots=True)
class MarkerRecord:
    pass_num: int
    label: str
    at: Offset
    symbol: str


@dataclass(frozen=True, slots=True)
class BoxRecord:
    pass_num: int
    bounds: Bounds


@dataclass(frozen=True, slots=True)
class StatsRecord:
    title: str
    columns: tuple[tuple[str, str], ...]
    rows: tuple[dict[str, str], ...]
    footer: str = ""


@dataclass(frozen=True, slots=True)
class PassGroupStats:
    freqs: tuple[float, ...]
    moved: Offset
    result: Offset
    sample_count: int

    def format_result(self) -> str:
        return f"({self.result.x:+.4f}, {self.result.y:+.4f})"

    def format_moved(self) -> str:
        return f"({self.moved.x:.4f}, {self.moved.y:.4f})"

    def format_freq_range(self) -> str:
        if not self.freqs:
            return "n/a"
        return f"[{min(self.freqs):.0f}, {max(self.freqs):.0f}]"


def pass_record_stats(record: _Record) -> PassGroupStats:
    if isinstance(record, SweepCentroidPassRecord):
        freqs = record.samples.freqs or ()
        return PassGroupStats(
            freqs=freqs,
            moved=record.move.moved,
            result=record.move.result,
            sample_count=len(record.samples.xs),
        )
    if isinstance(record, CentroidPassRecord):
        freqs = record.probes.freqs or ()
        return PassGroupStats(
            freqs=freqs,
            moved=record.move.moved,
            result=record.move.result,
            sample_count=len(record.probes.xs),
        )
    raise TypeError(f"unsupported pass record type: {type(record).__name__}")


def pass_group_stats(group: Sequence[Any]) -> PassGroupStats:
    for record in group:
        if isinstance(record, (SweepCentroidPassRecord, CentroidPassRecord)):
            return pass_record_stats(record)
    raise ValueError("no pass record found in group")


def final_result_offset(records: Sequence[_Record]) -> Offset:
    best_pass = 0
    best_result = Offset.zero()
    for record in records:
        pass_num = record_pass_num(record)
        move = getattr(record, "move", None)
        if pass_num is None or move is None:
            continue
        if pass_num >= best_pass:
            best_pass = pass_num
            best_result = move.result
    return best_result


def add_scatter(
    fig: Any,
    record: ScatterRecord,
    search_for: Literal["min", "max"],
    color: str,
    *,
    marker_size: int = 7,
    marker_opacity: float = 1.0,
    row: int | None = None,
    col: int | None = None,
) -> None:
    if go is None or not record.cloud.xs:
        return
    mode = record.mode.value if isinstance(record.mode, ScatterMode) else record.mode
    freqs = record.cloud.freqs
    marker: dict[str, Any]
    if freqs is not None:
        marker = freq_marker(
            list(freqs),
            search_for,
            size=marker_size,
            opacity=marker_opacity,
        )
    else:
        marker = {"size": marker_size, "color": color, "opacity": marker_opacity}
    trace = go.Scatter(
        x=list(record.cloud.xs),
        y=list(record.cloud.ys),
        mode=mode,
        name=record.label,
        line={"color": color, "width": 1},
        marker=marker,
        text=([f"{freq:.1f} Hz" for freq in freqs] if freqs is not None else None),
        hovertemplate=(
            f"{record.label}<br>x=%{{x:.4f}} y=%{{y:.4f}} %{{text}}<extra></extra>"
        ),
        legendgroup=record.label,
    )
    if row is not None and col is not None:
        fig.add_trace(trace, row=row, col=col)
    else:
        fig.add_trace(trace)


def add_marker(
    fig: Any,
    record: MarkerRecord,
    color: str,
    *,
    size: int = 11,
    row: int | None = None,
    col: int | None = None,
) -> None:
    if go is None:
        return
    trace = go.Scatter(
        x=[record.at.x],
        y=[record.at.y],
        mode="markers",
        name=record.label,
        marker={
            "size": size,
            "symbol": record.symbol,
            "color": color,
            "line": (
                {"width": 1, "color": marker_outline()}
                if record.symbol == "star"
                else None
            ),
        },
        legendgroup=record.label,
        showlegend=record.symbol == "star",
    )
    if row is not None and col is not None:
        fig.add_trace(trace, row=row, col=col)
    else:
        fig.add_trace(trace)


def add_box(
    fig: Any,
    record: BoxRecord,
    color: str,
    *,
    row: int | None = None,
    col: int | None = None,
) -> None:
    shape = {
        "type": "rect",
        "x0": record.bounds.lo.x,
        "x1": record.bounds.hi.x,
        "y0": record.bounds.lo.y,
        "y1": record.bounds.hi.y,
        "line": {"color": color, "width": 1, "dash": "dot"},
        "fillcolor": "rgba(0,0,0,0)",
    }
    if row is not None and col is not None:
        fig.add_shape(**shape, row=row, col=col)
    else:
        fig.add_shape(**shape)


def layout_with_stats(
    fig: Any,
    stats: StatsRecord,
    *,
    xaxis_title: str = "X offset (mm)",
    yaxis_title: str = "Y offset (mm)",
) -> None:
    fig.update_layout(
        xaxis_title=xaxis_title,
        yaxis_title=yaxis_title,
        **single_xy_layout(
            title=stats.title,
            tables=[header_table(list(stats.columns), list(stats.rows))],
            final=stats.footer,
        ),
    )
    apply_axes_theme(fig)


def render_pass_xy_figure(
    pass_records: Sequence[CentroidPassRecord | SweepCentroidPassRecord],
    *,
    search_for: Literal["min", "max"],
    draw_bounds: bool,
    extra_columns: tuple[tuple[str, str], ...],
    title_prefix: str,
) -> Any | None:
    if go is None or not pass_records:
        return None

    fig = go.Figure()
    pass_rows: list[dict[str, str]] = []
    pass_nums = sorted(record.pass_num for record in pass_records)
    base_columns = (
        ("pass", "Pass"),
        *extra_columns,
        ("result", "Result (mm)"),
        ("moved", "Moved (mm)"),
        ("freq", "Freq (Hz)"),
    )

    for record in sorted(pass_records, key=lambda item: item.pass_num):
        pass_num = record.pass_num
        color = pass_color(pass_num)
        if isinstance(record, CentroidPassRecord):
            label = f"pass {pass_num}"
            cloud = record.probes
            scatter_suffix = "probes"
            scatter_mode = ScatterMode.MARKERS_LINES
            result_star_size = 14 if pass_num == pass_nums[-1] else 11
        else:
            label = f"pass {pass_num} ({record.phase})"
            cloud = record.samples
            scatter_suffix = "samples"
            scatter_mode = ScatterMode.MARKERS
            result_star_size = 11

        add_scatter(
            fig,
            ScatterRecord(
                pass_num,
                f"{label} {scatter_suffix}",
                cloud,
                mode=scatter_mode,
            ),
            search_for,
            color,
        )
        if draw_bounds and isinstance(record, SweepCentroidPassRecord):
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
            size=result_star_size,
        )
        stats = pass_group_stats([record])
        row: dict[str, str] = {
            "pass": str(pass_num),
            "result": stats.format_result(),
            "moved": stats.format_moved(),
            "freq": stats.format_freq_range(),
        }
        if isinstance(record, SweepCentroidPassRecord):
            row["phase"] = record.phase
            row["samples"] = str(stats.sample_count)
        pass_rows.append(row)

    final = final_result_offset(pass_records)
    layout_with_stats(
        fig,
        StatsRecord(
            title=(
                f"{title_prefix} ({len(pass_records)} pass"
                f"{'' if len(pass_records) == 1 else 'es'})  search={search_for}"
            ),
            columns=base_columns,
            rows=tuple(pass_rows),
            footer=f"Final: ({final.x:+.4f}, {final.y:+.4f}) mm",
        ),
    )
    return fig
