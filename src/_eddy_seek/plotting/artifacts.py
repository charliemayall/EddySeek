"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Plot artifact I/O - filenames and HTML export.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..common import session_artifact_filename
from ._plotly import plotly_available, write_html

if TYPE_CHECKING:
    from ..session import SeekSession

logger = logging.getLogger(__name__)


def generate_plot_filename(
    when: datetime | None = None,
    *,
    suffix: str = "",
    run_label: str = "run",
    run_id: str | None = None,
) -> str:
    return session_artifact_filename(
        when,
        suffix=suffix,
        run_label=run_label,
        run_id=run_id,
        ext="html",
    )


def write_figure(
    results_dir: Path,
    fig: Any,
    *,
    write_at: datetime | None = None,
    suffix: str = "",
    run_label: str = "run",
    run_id: str | None = None,
) -> str | None:
    if not plotly_available() or fig is None:
        return None
    out_path = results_dir / generate_plot_filename(
        write_at,
        suffix=suffix,
        run_label=run_label,
        run_id=run_id,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not write_html(str(out_path), fig):
        logger.warning(f"eddy_seek: failed to write plot to {out_path}")
        return None
    logger.info(f"eddy_seek: debug plot saved to {out_path}")
    return str(out_path)


def finalize_strategy_plot(ctx: SeekSession, strategy_name: str) -> str | None:
    from .registry import render_session_plot

    if not ctx.config.save_plots:
        return None
    fig = render_session_plot(
        strategy_name,
        ctx.recorder.records(),
        search_for=ctx.config.search_for,
    )
    if fig is None:
        return None
    return write_figure(
        Path(ctx.config.result_folder),
        fig,
        write_at=ctx.artifact_write_at,
        suffix=ctx.artifact_suffix(strategy_name),
        run_label=ctx.run_label,
        run_id=ctx.run_id,
    )
