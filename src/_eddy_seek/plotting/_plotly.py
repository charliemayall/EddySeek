"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Optional Plotly import and HTML export helper.
"""

from __future__ import annotations

import html as html_module
import logging
import textwrap
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
except (ImportError, ModuleNotFoundError):
    go = None  # ty: ignore[invalid-assignment]
    make_subplots = None  # ty: ignore[invalid-assignment]


PASS_COLORS = (
    "#636EFA",
    "#EF553B",
    "#00CC96",
    "#AB63FA",
    "#FFA15A",
    "#19D3F3",
    "#FF6692",
    "#B6E880",
    "#FF97FF",
    "#FECB52",
)


def pass_color(pass_num: int) -> str:
    return PASS_COLORS[(pass_num - 1) % len(PASS_COLORS)]


COLORSCALE = "sunsetdark"
_EDDYSEEK_REPO = "https://github.com/charlie-mayall/EddySeek"
_AXIS_TICK_SIZE = 9
_AXIS_TITLE_SIZE = 10
_BASE_FONT_SIZE = 10
_DEBUG_ROW_HEIGHT_PX = 500


class THEME_COLORS:
    """Yoinked from Tailwind slate palette."""

    background = "#0f172a"
    page = "#1e293b"
    text = "#f8fafc"
    muted = "#94a3b8"
    plot = "#334155"


_AXIS_THEME: dict[str, Any] = {
    "gridcolor": THEME_COLORS.muted,
    "zerolinecolor": THEME_COLORS.muted,
    "linecolor": THEME_COLORS.muted,
    "tickfont": {"color": THEME_COLORS.text, "size": _AXIS_TICK_SIZE},
    "title_font": {"color": THEME_COLORS.text, "size": _AXIS_TITLE_SIZE},
}

_PAGE_CSS = f"""\
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: {THEME_COLORS.background};
  color: {THEME_COLORS.text};
  font-family: system-ui, -apple-system, sans-serif;
  font-size: 13px;
  line-height: 1.45;
  min-height: 100vh;
}}
.page {{
  display: flex;
  flex-direction: column;
  align-items: center;
  min-height: 100vh;
  padding: 0.35rem;
  gap: 0.35rem;
}}
.flex-row {{
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.35rem;
}}
.stats {{
  flex-shrink: 0;
  width: 100%;
  max-width: min(960px, 100%);
}}
.stats .flex-row h1 {{
  font-size: 1rem;
  font-weight: 600;
  margin-bottom: 0;
}}
.es-brand {{
  font-size: 12px;
  color: {THEME_COLORS.muted};
  text-decoration: underline;
  white-space: nowrap;
}}
.stats .final {{
  margin-top: 0.35rem;
  font-size: 0.85rem;
  font-weight: 500;
}}
.stats-table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8rem;
  margin-top: 0.25rem;
}}
.stats-table + .stats-table {{
  margin-top: 0.5rem;
}}
.stats-table th,
.stats-table td {{
  padding: 0.2rem 0.45rem;
  text-align: left;
  border-bottom: 1px solid {THEME_COLORS.plot};
}}
.stats-table th {{
  color: {THEME_COLORS.muted};
  font-weight: 500;
}}
.stats-table td {{
  color: {THEME_COLORS.text};
  font-variant-numeric: tabular-nums;
}}
.chart {{
  flex: 0 0 auto;
  display: flex;
  justify-content: center;
  align-items: center;
  width: 100%;
  max-width: min(960px, 100%);
  border-left: 3px solid {THEME_COLORS.plot};
}}
.chart-inner--square {{
  aspect-ratio: 1 / 1;
  width: 100%;
  max-width: min(960px, 100%);
}}
.chart-inner--wide {{
  width: 100%;
}}
.chart-inner .plotly-graph-div,
.chart-inner .js-plotly-plot {{
  width: 100% !important;
  height: 100% !important;
}}
"""


def plotly_available() -> bool:
    return go is not None and make_subplots is not None


def marker_outline() -> str:
    """Star/circle outline that reads on the dark plot background."""
    return THEME_COLORS.page


def freq_marker(
    freqs: list[float],
    search_for: Literal["min", "max"],
    *,
    size: int = 5,
    opacity: float = 0.75,
) -> dict[str, Any]:
    """Scatter marker dict with frequency colorscale (COLORSCALE)."""
    return {
        "size": size,
        "color": freqs,
        "colorscale": COLORSCALE,
        "reversescale": search_for == "min",
        "opacity": opacity,
        "colorbar": {
            "title": {
                "text": "Hz",
                "font": {"color": THEME_COLORS.text, "size": _AXIS_TITLE_SIZE},
            },
            "tickfont": {"color": THEME_COLORS.text, "size": _AXIS_TICK_SIZE},
            "x": 1.02,
            "xanchor": "left",
            "len": 0.75,
            "thickness": 12,
        },
    }


def header_table(
    columns: list[tuple[str, str]], rows: list[dict[str, str]]
) -> dict[str, Any]:
    """One keyed table: column key -> header label, each row is field -> cell value."""
    return {"columns": columns, "rows": rows}


def session_header_meta(
    title: str,
    *,
    tables: list[dict[str, Any]],
    final: str = "",
) -> dict[str, Any]:
    """Structured session stats for the HTML page header."""
    return {"title": title, "tables": tables, "final": final}


def apply_theme(layout: dict[str, Any]) -> dict[str, Any]:
    """Merge dark-slate colors and typography into a layout dict."""
    legend = dict(layout.get("legend") or {})
    legend.setdefault("font", {"color": THEME_COLORS.text, "size": _BASE_FONT_SIZE})
    legend.setdefault("bgcolor", "rgba(0,0,0,0)")

    themed: dict[str, Any] = {
        **layout,
        "paper_bgcolor": THEME_COLORS.background,
        "plot_bgcolor": THEME_COLORS.plot,
        "font": {"color": THEME_COLORS.text, "size": _BASE_FONT_SIZE},
        "legend": legend,
    }
    for key, value in layout.items():
        if (key.startswith("xaxis") or key.startswith("yaxis")) and isinstance(
            value, dict
        ):
            themed[key] = {**_AXIS_THEME, **value}
    if "xaxis" not in themed and "yaxis" not in themed:
        themed["xaxis"] = dict(_AXIS_THEME)
        themed["yaxis"] = dict(_AXIS_THEME)
    return themed


def apply_axes_theme(fig: Any) -> None:
    """Theme every axis on a figure (including subplot axes)."""
    fig.update_xaxes(**_AXIS_THEME)
    fig.update_yaxes(**_AXIS_THEME)


def single_xy_layout(
    *,
    title: str,
    tables: list[dict[str, Any]],
    final: str,
) -> dict[str, Any]:
    """Responsive square XY layout; session stats live in layout.meta for HTML export."""
    return apply_theme(
        {
            "autosize": True,
            "margin": {"l": 44, "r": 68, "t": 8, "b": 52, "pad": 0},
            "yaxis": {"scaleanchor": "x", "scaleratio": 1},
            "legend": {"orientation": "h", "y": -0.12, "x": 0, "xanchor": "left"},
            "title": None,
            "meta": {
                "eddy_header": session_header_meta(title, tables=tables, final=final),
                "eddy_chart": "square",
            },
        }
    )


def multi_panel_layout(
    *,
    rows: int,
    cols: int,
    title: str,
    tables: list[dict[str, Any]],
    final: str,
    row_height_px: int | None = None,
) -> dict[str, Any]:
    """Responsive multi-panel layout with HTML header meta."""
    _ = cols
    height_px = row_height_px * rows if row_height_px is not None else None
    meta: dict[str, Any] = {
        "eddy_header": session_header_meta(title, tables=tables, final=final),
        "eddy_chart": "wide",
    }
    if height_px is not None:
        meta["eddy_chart_height_px"] = height_px
    layout_dict: dict[str, Any] = {
        "autosize": True,
        "margin": {"l": 32, "r": 12, "t": 24, "b": 32, "pad": 0},
        "legend": {"orientation": "h", "y": -0.01, "x": 0, "xanchor": "left"},
        "title": None,
        "meta": meta,
    }
    if height_px is not None:
        layout_dict["height"] = height_px
    return apply_theme(layout_dict)


def xy_session_layout(
    title: str,
    *,
    columns: list[tuple[str, str]],
    rows: list[dict[str, str]],
    final: str,
) -> dict[str, Any]:
    """Square XY plot layout with session stats in HTML header meta."""
    return single_xy_layout(
        title=title,
        tables=[header_table(columns, rows)],
        final=final,
    )


def _layout_meta(fig: Any) -> dict[str, Any]:
    meta = fig.layout.meta
    if meta is None:
        return {}
    if isinstance(meta, dict):
        return meta
    return dict(meta)


def _render_table(table: dict[str, Any]) -> str:
    columns = table.get("columns") or []
    rows = table.get("rows") or []
    if not columns or not rows:
        return ""
    keys = [key for key, _ in columns]
    parts = ['<table class="stats-table"><thead><tr>']
    for _, label in columns:
        parts.append(f"<th>{html_module.escape(label)}</th>")
    parts.append("</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>")
        parts.extend(
            [f"<td>{html_module.escape(str(row.get(key, '')))}</td>" for key in keys]
        )

        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _render_header(header: dict[str, Any]) -> str:
    if not header:
        return ""
    title = html_module.escape(str(header.get("title", "")))
    tables_html = "".join(_render_table(table) for table in header.get("tables", []))
    final = html_module.escape(str(header.get("final", "")))
    final_html = f'<p class="final">{final}</p>' if final else ""
    brand = (
        f'<a class="es-brand" href="{_EDDYSEEK_REPO}" '
        'target="_blank" rel="noopener noreferrer">Made with EddySeek</a>'
    )
    return "".join(
        [
            '<header class="stats">',
            f'<div class="flex-row"><h1>{title}</h1>{brand}</div>',
            f"{tables_html}",
            f"{final_html}",
            "</header>",
        ]
    )


def _html_shell(
    plot_div: str,
    *,
    header_html: str,
    chart_class: str,
    chart_height_px: int | None = None,
) -> str:
    height_attr = (
        f' style="height: {chart_height_px}px; min-height: {chart_height_px}px;'
        f' max-height: {chart_height_px}px;"'
        if chart_height_px is not None
        else ""
    )
    return textwrap.dedent(
        f"""\
        <!DOCTYPE html>
        <html lang="en">
        <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>EddySeek plot</title>
        <style>{_PAGE_CSS}</style>
        </head>
        <body>
        <div class="page">
        {header_html}
        <main class="chart">
        <div class="chart-inner chart-inner--{chart_class}"{height_attr}>
        {plot_div}
        </div>
        </main>
        </div>
        </body>
        </html>
        """
    )


def write_html(path: str | Path, fig: Any) -> bool:
    if not plotly_available():
        return False
    if go is None:
        return False
    try:
        meta = _layout_meta(fig)
        header = meta.get("eddy_header") or {}
        chart_class = meta.get("eddy_chart", "square")
        chart_height_px = meta.get("eddy_chart_height_px")
        plot_div = fig.to_html(
            full_html=False,
            include_plotlyjs="cdn",
            config={"responsive": True, "displayModeBar": True},
        )
        document = _html_shell(
            plot_div,
            header_html=_render_header(header),
            chart_class=chart_class,
            chart_height_px=chart_height_px,
        )
        Path(path).write_text(document, encoding="utf-8")
        return True
    except OSError as exc:
        logger.warning(f"eddy_seek: failed to write plot to {path}: {exc}")
        return False
