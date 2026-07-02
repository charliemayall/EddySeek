"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging

from ..common import Position
from ..session import SeekContext, SeekReporter

logger = logging.getLogger(__name__)


class SeekStrategy(ABC):
    """XY search algorithm selected by ``SeekConfig.strategy``."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    def announce_start(self, ctx: SeekContext, reporter: SeekReporter) -> None:
        pass

    def on_session_end(self, ctx: SeekContext) -> str | None:
        return None

    def search(self, ctx: SeekContext, reporter: SeekReporter) -> tuple[Position, int]:
        cfg = ctx.config
        best = Position.zero()
        passes_run = 0

        for pass_num in range(1, cfg.max_passes + 1):
            passes_run = pass_num
            logger.debug(
                "eddy_seek: %s pass %d start best=(%.4f, %.4f)",
                self.name,
                pass_num,
                best.x,
                best.y,
            )
            new = self._step(ctx, pass_num, best)
            moved = (new - best).abs_components()
            reporter.info(self._pass_message(pass_num, new, moved, ctx))
            best = new

            if moved.x < cfg.tolerance and moved.y < cfg.tolerance:
                logger.debug(
                    "eddy_seek: %s converged after pass %d (moved %.4f, %.4f)",
                    self.name,
                    pass_num,
                    moved.x,
                    moved.y,
                )
                reporter.info(f"EDDY_SEEK: converged after {pass_num} pass(es).")
                break
        else:
            logger.debug(
                "eddy_seek: %s hit max_passes=%d without convergence",
                self.name,
                cfg.max_passes,
            )
            reporter.info(
                f"EDDY_SEEK: reached max_passes={cfg.max_passes} "
                f"without full convergence - using best result."
            )

        return best, passes_run

    @abstractmethod
    def _step(self, ctx: SeekContext, pass_num: int, best: Position) -> Position: ...

    @abstractmethod
    def _pass_message(
        self,
        pass_num: int,
        new: Position,
        moved: Position,
        ctx: SeekContext,
    ) -> str: ...
