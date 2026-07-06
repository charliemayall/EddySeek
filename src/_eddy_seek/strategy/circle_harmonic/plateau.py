"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Radius tier, anchor, and freeze state for circle-harmonic search.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from ...common import Offset
from ...config import SeekConfig

if TYPE_CHECKING:
    from .circle_pass import CirclePassOutcome

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CircleHarmonicMode:
    skip_bootstrap: bool
    refresh_sweeps: bool

    @classmethod
    def from_config(cls, cfg: SeekConfig) -> CircleHarmonicMode:
        return cls(
            skip_bootstrap=cfg.circle_skip_bootstrap,
            refresh_sweeps=cfg.circle_refresh_sweeps,
        )


class PassAction(Enum):
    CONTINUE = "continue"
    STOP = "stop"
    RETRY = "retry"


def is_min_radius(trace_radius: float, radius_min: float) -> bool:
    return trace_radius <= radius_min + 1e-9


def is_below_min_radius(trace_radius: float, radius_min: float) -> bool:
    return trace_radius < radius_min - 1e-9


@dataclass(slots=True)
class PlateauState:
    tier: int = 0
    anchor: Offset | None = None
    frozen: Offset | None = None
    last_rejected: bool = False

    def reset(self) -> None:
        self.tier = 0
        self.anchor = None
        self.frozen = None
        self.last_rejected = False

    def estimate(self, best: Offset) -> Offset:
        return self.anchor if self.anchor is not None else best

    def advance(
        self,
        outcome: CirclePassOutcome,
        *,
        radius_min: float,
        bootstrap: Offset,
    ) -> tuple[PassAction, Offset]:
        at_min = is_min_radius(outcome.trace_radius, radius_min)

        if outcome.rejected:
            self.last_rejected = True
            if at_min:
                hold = self.anchor if self.anchor is not None else outcome.result
                self.frozen = hold
                logger.info(
                    f"eddy_seek: circle_harmonic at min radius "
                    f"r={outcome.trace_radius:.4f} - stopping after rejected pass"
                )
                return PassAction.STOP, hold
            self.tier += 1
            logger.info(
                f"eddy_seek: circle_harmonic radius tier -> {self.tier} (reject)"
            )
            step_result = self.anchor if self.anchor is not None else outcome.result
            return PassAction.RETRY, step_result

        self.last_rejected = False
        if not outcome.freeze:
            self.anchor = outcome.result
        if outcome.freeze:
            self.frozen = outcome.result
            return PassAction.STOP, outcome.result
        return PassAction.CONTINUE, outcome.result

    def plot_position(
        self,
        outcome: CirclePassOutcome,
        *,
        bootstrap: Offset,
    ) -> Offset | None:
        if outcome.rejected:
            return self.anchor if self.anchor is not None else bootstrap
        if outcome.freeze and not outcome.samples:
            return None
        if not outcome.samples:
            return None
        return outcome.result
