"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from ..common import ConvergenceError, Offset
from ..kconsole import KConsole
from ..session import SeekSession

logger = logging.getLogger(__name__)

_DIVERGE_TOL = 1.25


def _check_pass_divergence(
    positions: list[Offset],
    *,
    tolerance: float,
    pass_num: int,
) -> None:
    if len(positions) < 3:
        return
    prev, cur, nxt = positions[-3], positions[-2], positions[-1]
    d1 = prev.distance_to(cur)
    if d1 < tolerance:
        return
    d2 = cur.distance_to(nxt)
    if d2 > _DIVERGE_TOL * d1:
        raise RuntimeError(
            f"eddy_seek: pass corrections diverging at pass {pass_num}: "
            f"correction {d2:.4f} mm > {_DIVERGE_TOL} × {d1:.4f} mm"
        )


class SeekStrategy(ABC):
    """XY search algorithm selected by ``SeekConfig.strategy``."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def announce_start(self, ctx: SeekSession, console: KConsole) -> None: ...

    def on_session_end(self, ctx: SeekSession) -> str | None:
        return None

    def search(self, ctx: SeekSession, console: KConsole) -> tuple[Offset, int]:
        cfg = ctx.config
        best = Offset.zero()
        positions = [best]
        passes_run = 0

        for pass_num in range(1, cfg.max_passes + 1):
            passes_run = pass_num
            logger.info(
                f"eddy_seek: {self.name} pass {pass_num} start "
                f"best=({best.x:.4f}, {best.y:.4f})"
            )
            new = self._step(ctx, pass_num, best)
            moved = (new - best).abs_components()
            console.info(self._pass_message(pass_num, new, moved, ctx))
            positions.append(new)
            if self.should_check_divergence(ctx, pass_num):
                _check_pass_divergence(
                    positions, tolerance=cfg.tolerance, pass_num=pass_num
                )
            best = new

            if moved.x < cfg.tolerance and moved.y < cfg.tolerance:
                logger.info(
                    f"eddy_seek: {self.name} converged after pass {pass_num} "
                    f"(moved {moved.x:.4f}, {moved.y:.4f})"
                )
                break
        else:
            raise ConvergenceError(
                self.name,
                max_passes=cfg.max_passes,
                tolerance=cfg.tolerance,
            )

        return best, passes_run

    def should_check_divergence(self, ctx: SeekSession, pass_num: int) -> bool:
        return True

    @abstractmethod
    def _step(self, ctx: SeekSession, pass_num: int, best: Offset) -> Offset: ...

    @abstractmethod
    def _pass_message(
        self,
        pass_num: int,
        new: Offset,
        moved: Offset,
        ctx: SeekSession,
    ) -> str: ...
