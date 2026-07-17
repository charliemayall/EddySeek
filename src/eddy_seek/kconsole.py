"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Klipper console output with echo:/!! prefixes and logger pairing.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, NoReturn

from .common import StrEnum

if TYPE_CHECKING:
    from klippy.gcode import GCodeCommand

    from .config import SeekConfig

logger = logging.getLogger(__name__)


class ConsoleSymbols(StrEnum):
    WARN = "⚠️"
    ERROR = "❌"
    PLOT = "📊"
    BR = "</br>"


QueueType = Literal["warn", "info", "error"]
_QUEUE_LOGGERS: dict[QueueType, str] = {
    "warn": "warning",
    "error": "error",
}


class KConsole:
    """Wrap ``GCodeCommand.respond_raw`` for user-facing output."""

    prefix: str = "ES"
    _queue: ClassVar[list[tuple[QueueType, str]]] = []

    @classmethod
    def queue(cls, msg: str, *, type: QueueType = "warn") -> None:
        """Defer output until the next ``KConsole`` is constructed."""
        cls._queue.append((type, msg))
        log_fn = _QUEUE_LOGGERS.get(type)
        if log_fn is not None:
            getattr(logger, log_fn)(f"eddy_seek: {msg}")

    @classmethod
    def clear_queue(cls) -> None:
        """Discard queued messages (tests)."""
        cls._queue.clear()

    @classmethod
    def pending(cls) -> list[tuple[QueueType, str]]:
        """Queued messages not yet flushed (tests)."""
        return list(cls._queue)

    def __init__(
        self,
        gcmd: GCodeCommand,
        cfg: SeekConfig,
        *,
        verbose: bool | None = None,
    ) -> None:
        self._gcmd = gcmd
        if verbose is not None:
            self.verbose = verbose
        else:
            from_param = False
            try:
                raw = gcmd.get("VERBOSE", "0")
                from_param = str(raw).strip().lower() in ("1", "true", "yes", "on")
            except Exception:
                pass
            self.verbose = from_param or cfg.debug
        self._flush_queue()

    def _flush_queue(self) -> None:
        for msg_type, msg in type(self)._queue:
            getattr(self, msg_type)(msg)
        type(self)._queue.clear()

    def _emit(self, klipper_prefix: str, msg: str) -> None:
        self._gcmd.respond_raw(f"{klipper_prefix}{msg}")

    def _prefix_msg(self, msg: str) -> str:
        return f"{self.prefix}: {msg}"

    def entry(self, msg: str) -> None:
        """First message of a command flow (includes prefix)."""
        self._emit("echo: ", self._prefix_msg(msg))

    def exit(self, msg: str) -> None:
        """Last message of a command flow (includes prefix)."""
        self._emit("echo: ", self._prefix_msg(msg))

    def info(self, msg: str) -> None:
        """Use for info lines without prefix."""
        self._emit("echo: ", msg)

    def plot_saved(self, plot_path: str | Path) -> None:
        """Report a saved plot with its full resolved path."""
        self.info(f"{ConsoleSymbols.PLOT} Plot saved: {Path(plot_path)}")

    def warn_plot_missing(self) -> None:
        """Warn when save_plots is on but no plot was produced."""
        self.warn(
            "save_plots is enabled but no plot was written (is plotly installed?)"
        )
        logger.warning("eddy_seek: save_plots enabled but no plot was written")

    def warn(self, msg: str) -> None:
        """
        Prints a warning message with this format:

        ⚠️ ⚠️ ⚠️

        WARNING: {msg}

        ⚠️ ⚠️ ⚠️

        """
        self._emit(
            "echo: ",
            f"{(ConsoleSymbols.WARN + '  ') * 3}"
            f"{ConsoleSymbols.BR}"
            f"WARNING: {msg}{ConsoleSymbols.BR}"
            f"{(ConsoleSymbols.WARN + '  ') * 3}",
        )

    def detail(self, msg: str) -> None:
        """Print a info line, but only when KConsole.verbose is True."""
        if self.verbose:
            self._emit("echo: ", msg)

    def error(self, msg: str) -> None:
        self._emit("!! ", msg)

    def raise_error(self, msg: str) -> NoReturn:
        # do not print here as Klipper will print the error message automatically
        raise self._gcmd.error(msg)
