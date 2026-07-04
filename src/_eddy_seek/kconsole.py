"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Klipper console output with echo:/!! prefixes and logger pairing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn, Protocol

if TYPE_CHECKING:
    from klippy.gcode import GCodeCommand

    from .config import SeekConfig

logger = logging.getLogger(__name__)


class SeekReporter(Protocol):
    def info(self, msg: str) -> None: ...


class Emoji:
    WARN = "⚠️"
    ERROR = "❌"
    PLOT = "📊"


class KConsole:
    """Wrap ``GCodeCommand.respond_raw`` for user-facing output."""

    prefix: str = "ES"
    BR = "</br>"

    def __init__(self, gcmd: GCodeCommand, *, verbose: bool = False) -> None:
        self._gcmd = gcmd
        self.verbose = verbose

    def _emit(
        self, klipper_prefix: str, msg: str, *, log_level: int = logging.INFO
    ) -> None:
        self._gcmd.respond_raw(f"{klipper_prefix}{msg}")
        logger.log(log_level, f"eddy_seek: {msg}")

    def _prefix_msg(self, msg: str) -> str:
        return f"{self.prefix}: {msg}"

    def entry(self, msg: str) -> None:
        """First message of a command flow (includes ``ES:`` prefix)."""
        self._emit("echo: ", self._prefix_msg(msg))

    def exit(self, msg: str) -> None:
        """Last message of a command flow (includes ``ES:`` prefix)."""
        self._emit("echo: ", self._prefix_msg(msg))

    def info(self, msg: str) -> None:
        """Progress line without ``ES:`` prefix."""
        self._emit("echo: ", msg)

    def plot_saved(self, plot_path: str | Path) -> None:
        """Report a saved plot with its full resolved path."""
        self.info(f"{Emoji.PLOT} Plot saved: {Path(plot_path)}")

    def warn(self, msg: str) -> None:
        self._emit(
            "echo: ",
            f"{(Emoji.WARN + '  ') * 3}"
            f"{self.BR}"
            f"WARNING: {msg}{self.BR}"
            f"{(Emoji.WARN + '  ') * 3}",
        )

    def error(self, msg: str) -> None:
        self._emit("!! ", msg, log_level=logging.ERROR)

    def detail(self, msg: str) -> None:
        if self.verbose:
            self._emit("echo: ", msg)
        else:
            logger.debug(f"eddy_seek: {msg}")

    def fail(self, msg: str) -> NoReturn:
        raise self._gcmd.error(msg)


def console_for_gcmd(gcmd: GCodeCommand, cfg: SeekConfig) -> KConsole:
    """
    Create a console for the given gcmd and seek config.
    Verbosity is determined by the VERBOSE gcode param or debug config.
    """
    verbose = False
    try:
        raw = gcmd.get("VERBOSE", "0")
        verbose = str(raw).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        pass
    console = KConsole(gcmd, verbose=verbose or cfg.debug)
    return console
