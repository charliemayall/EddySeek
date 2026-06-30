"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

XY search algorithms for eddy_seek.

Imported by ``eddy_seek``; not a loadable Klipper config section.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
from typing import TYPE_CHECKING

from .common import Axis, Position

if TYPE_CHECKING:
    from .session import SeekSession

logger = logging.getLogger(__name__)


class SeekStrategy(ABC):
    """XY search algorithm selected by ``SeekConfig.strategy``."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    def announce_start(self, ctx: SeekSession, gcmd) -> None:
        # optional
        pass

    def search(self, ctx: SeekSession, gcmd) -> tuple[Position, int]:
        cfg = ctx.config
        best = Position(0.0, 0.0)
        passes_run = 0

        for pass_num in range(1, cfg.max_passes + 1):
            passes_run = pass_num
            new = self._step(ctx, pass_num, best)
            moved = Position(abs(new.x - best.x), abs(new.y - best.y))
            gcmd.respond_info(self._pass_message(pass_num, new, moved, ctx))
            best = new

            if moved.x < cfg.tolerance and moved.y < cfg.tolerance:
                gcmd.respond_info(f"EDDY_SEEK: converged after {pass_num} pass(es).")
                break
        else:
            gcmd.respond_info(
                f"EDDY_SEEK: reached max_passes={cfg.max_passes} "
                f"without full convergence - using best result."
            )

        return best, passes_run

    @abstractmethod
    def _step(self, ctx: SeekSession, pass_num: int, best: Position) -> Position: ...

    @abstractmethod
    def _pass_message(
        self,
        pass_num: int,
        new: Position,
        moved: Position,
        ctx: SeekSession,
    ) -> str: ...


class TernaryStrategy(SeekStrategy):
    @property
    def name(self) -> str:
        return "ternary"

    def _step(self, ctx: SeekSession, pass_num: int, best: Position) -> Position:
        cfg = ctx.config
        new_x = self._ternary_search_1d(
            ctx, axis=Axis.x, center=best.x, half_range=cfg.max_jog_x, fixed=best.y
        )
        new_y = self._ternary_search_1d(
            ctx, axis=Axis.y, center=best.y, half_range=cfg.max_jog_y, fixed=new_x
        )
        return Position(new_x, new_y)

    def _pass_message(
        self,
        pass_num: int,
        new: Position,
        moved: Position,
        ctx: SeekSession,
    ) -> str:
        return (
            f"EDDY_SEEK pass {pass_num}: "
            f"X offset {new.x:+.4f} mm (moved {moved.x:.4f})  "
            f"Y offset {new.y:+.4f} mm (moved {moved.y:.4f})"
        )

    def _ternary_search_1d(
        self,
        ctx: SeekSession,
        axis: Axis,
        center: float,
        half_range: float,
        fixed: float,
    ) -> float:
        cfg = ctx.config
        # Clamp to +/-half_range from the session start (offset 0). half_range is
        # max_jog for this axis, so a best point that drifts toward the edge must
        # not let a later pass probe beyond the configured jog radius.
        lo = max(-half_range, center - half_range)
        hi = min(half_range, center + half_range)

        for _iteration in range(cfg.max_iter):
            span = hi - lo
            if span < cfg.tolerance:
                break

            m1 = lo + span / 3.0
            m2 = hi - span / 3.0

            if axis is Axis.x:
                f1 = ctx.measure_at(Position(m1, fixed))
                f2 = ctx.measure_at(Position(m2, fixed))
            else:
                f1 = ctx.measure_at(Position(fixed, m1))
                f2 = ctx.measure_at(Position(fixed, m2))

            if self._is_better(ctx, f1, f2):
                hi = m2
            else:
                lo = m1

        return (lo + hi) / 2.0

    def _is_better(self, ctx: SeekSession, f1: float, f2: float) -> bool:
        if ctx.config.search_for == "min":
            return f1 < f2
        return f1 > f2


class CentroidStrategy(SeekStrategy):
    @property
    def name(self) -> str:
        return "centroid"

    def announce_start(self, ctx: SeekSession, gcmd) -> None:
        cfg = ctx.config
        gcmd.respond_info(
            f"EDDY_SEEK: centroid grid_step=({cfg.grid_step_x},{cfg.grid_step_y}) mm"
        )

    def _step(self, ctx: SeekSession, pass_num: int, best: Position) -> Position:
        cfg = ctx.config
        shrink = 0.5 ** (pass_num - 1)
        return self._centroid_pass(
            ctx,
            best,
            cfg.grid_step_x * shrink,
            cfg.grid_step_y * shrink,
        )

    def _pass_message(
        self,
        pass_num: int,
        new: Position,
        moved: Position,
        ctx: SeekSession,
    ) -> str:
        cfg = ctx.config
        shrink = 0.5 ** (pass_num - 1)
        step_x = cfg.grid_step_x * shrink
        step_y = cfg.grid_step_y * shrink
        return (
            f"EDDY_SEEK pass {pass_num}: "
            f"centroid ({new.x:+.4f}, {new.y:+.4f}) mm  "
            f"(moved {moved.x:.4f}, {moved.y:.4f})  "
            f"grid_step=({step_x:.4f}, {step_y:.4f})"
        )

    def _centroid_pass(
        self,
        ctx: SeekSession,
        center: Position,
        step_x: float,
        step_y: float,
    ) -> Position:
        cfg = ctx.config
        probes: list[tuple[Position, float]] = []

        for dy_mul in (-1, 0, 1):
            for dx_mul in (-1, 0, 1):
                position = Position(
                    max(-cfg.max_jog_x, min(cfg.max_jog_x, center.x + dx_mul * step_x)),
                    max(-cfg.max_jog_y, min(cfg.max_jog_y, center.y + dy_mul * step_y)),
                )
                freq = ctx.measure_at(position)
                probes.append((position, freq))

        freqs = [freq for _, freq in probes]
        f_min = min(freqs)
        f_max = max(freqs)
        weights = [self._frequency_weight(ctx, f, f_min, f_max) for f in freqs]
        total_w = sum(weights)
        if total_w < 1e-9:
            logger.warning(
                "eddy_seek: flat frequency response on centroid grid - "
                "keeping centre (%.4f, %.4f)",
                center.x,
                center.y,
            )
            return center

        centroid_x = (
            sum(position.x * w for (position, _), w in zip(probes, weights)) / total_w
        )
        centroid_y = (
            sum(position.y * w for (position, _), w in zip(probes, weights)) / total_w
        )
        return Position(
            max(-cfg.max_jog_x, min(cfg.max_jog_x, centroid_x)),
            max(-cfg.max_jog_y, min(cfg.max_jog_y, centroid_y)),
        )

    def _frequency_weight(
        self, ctx: SeekSession, freq: float, f_min: float, f_max: float
    ) -> float:
        if ctx.config.search_for == "min":
            return max(f_max - freq, 0.0)
        return max(freq - f_min, 0.0)


_STRATEGIES: dict[str, type[SeekStrategy]] = {
    "ternary": TernaryStrategy,
    "centroid": CentroidStrategy,
}


def strategy_for(name: str) -> SeekStrategy:
    try:
        return _STRATEGIES[name]()
    except KeyError as exc:
        raise ValueError(
            f"eddy_seek: unknown strategy {name!r} "
            f"(known: {', '.join(sorted(_STRATEGIES))})"
        ) from exc
