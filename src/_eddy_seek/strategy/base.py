"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from ..common import Offset, StrEnum
from ..kconsole import KConsole

if TYPE_CHECKING:
    from ..session import SeekSession

logger = logging.getLogger(__name__)

_DIVERGE_TOL = 1.75


class SeekExitKind(StrEnum):
    """How a seek session or pass ended."""

    CONVERGED = "converged"
    MAX_PASSES = "max_passes"
    DIVERGENCE = "divergence"
    FLAT_RESPONSE = "flat_response"
    INSUFFICIENT_SAMPLES = "insufficient_samples"


class SeekExitError(RuntimeError):
    """Expected seek failure with a shared exit kind."""

    exit_kind: SeekExitKind

    def __init__(self, strategy: str, message: str, *, exit_kind: SeekExitKind) -> None:
        self.strategy = strategy
        self.exit_kind = exit_kind
        super().__init__(message)


class MaxPassesError(SeekExitError):
    """Search exhausted ``max_passes`` without converging."""

    def __init__(self, strategy: str, *, max_passes: int, tolerance: float) -> None:
        self.max_passes = max_passes
        self.tolerance = tolerance
        super().__init__(
            strategy,
            f"{strategy} hit max_passes={max_passes} "
            f"without convergence (tolerance={tolerance:.4f} mm)",
            exit_kind=SeekExitKind.MAX_PASSES,
        )


class DivergenceError(SeekExitError):
    """Pass corrections diverging — later step grew vs prior step."""

    def __init__(
        self,
        strategy: str,
        *,
        pass_num: int,
        prior_correction: float,
        correction: float,
        previous: Offset,
        multiplier: float = _DIVERGE_TOL,
    ) -> None:
        self.pass_num = pass_num
        self.prior_correction = prior_correction
        self.correction = correction
        self.previous = previous
        self.multiplier = multiplier
        super().__init__(
            strategy,
            f"{strategy} pass corrections diverging at pass {pass_num}: "
            f"correction {correction:.4f} mm > {multiplier} × {prior_correction:.4f} mm",
            exit_kind=SeekExitKind.DIVERGENCE,
        )


class InsufficientSamplesError(SeekExitError):
    """Sweep collected too few in-range samples."""

    def __init__(self, strategy: str, *, count: int, min_samples: int) -> None:
        self.count = count
        self.min_samples = min_samples
        super().__init__(
            strategy,
            f"eddy_seek: {strategy} collected {count} in-range samples "
            f"(need >= {min_samples}). "
            "Check sensor and sweep speed.",
            exit_kind=SeekExitKind.INSUFFICIENT_SAMPLES,
        )


def _check_pass_divergence(
    strategy: str,
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
        raise DivergenceError(
            strategy,
            pass_num=pass_num,
            prior_correction=d1,
            correction=d2,
            previous=cur,
        )


class SeekStrategy(ABC):
    """XY search algorithm selected by ``SeekConfig.strategy``."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def announce_start(self, ctx: SeekSession, console: KConsole) -> None: ...

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
                    self.name,
                    positions,
                    tolerance=cfg.tolerance,
                    pass_num=pass_num,
                )
            best = new

            if moved.x < cfg.tolerance and moved.y < cfg.tolerance:
                logger.info(
                    f"eddy_seek: {self.name} converged after pass {pass_num} "
                    f"(moved {moved.x:.4f}, {moved.y:.4f})"
                )
                break
        else:
            raise MaxPassesError(
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
