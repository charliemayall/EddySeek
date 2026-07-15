"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Console announcements for saved seek plots.
"""

from __future__ import annotations

from typing import Literal

from .kconsole import KConsole


def announce_seek_plot(
    console: KConsole,
    *,
    plot_path: str | None,
    status: Literal["ok", "failed"],
    save_plots: bool,
    enabled: bool = True,
) -> None:
    """Report a saved strategy plot or warn when save_plots is on but none was written."""
    if not enabled:
        return
    if plot_path is not None:
        console.plot_saved(plot_path)
    elif save_plots and status == "ok":
        console.warn_plot_missing()
