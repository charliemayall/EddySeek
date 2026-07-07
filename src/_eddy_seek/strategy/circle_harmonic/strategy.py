"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Circle-harmonic strategy: sweep bootstrap + guarded first-harmonic nulling.
"""

from __future__ import annotations

import logging

from ...common import Offset
from ...harmonic import HarmonicFit
from ...kconsole import KConsole
from ...movement.handler import MotionSample
from ...plotting.artifacts import finalize_strategy_plot
from ...plotting.primitives import (
    BinnedProfile,
    Bounds,
    CircleBootstrapRecord,
    CircleHarmonicPassRecord,
    PassMove,
    XYCloud,
)
from ...session import SeekSession
from ..base import MaxPassesError, SeekStrategy, _check_pass_divergence
from .bootstrap import bootstrap_pass
from .circle_pass import CirclePassOutcome, compute_circle_pass
from .plateau import CircleHarmonicMode, PlateauState

logger = logging.getLogger(__name__)


class CircleHarmonicStrategy(SeekStrategy):
    """Sweep bootstrap, then circle harmonic nulling with model and bore gates."""

    def __init__(self) -> None:
        self._bootstrap: Offset | None = None
        self._x_profile: list[tuple[float, float]] = []
        self._y_profile: list[tuple[float, float]] = []
        self._mode = CircleHarmonicMode(
            skip_bootstrap=False,
            refresh_sweeps=False,
        )
        self._plateau = PlateauState()

    @property
    def name(self) -> str:
        return "circle_harmonic"

    def announce_start(self, ctx: SeekSession, console: KConsole) -> None:
        mode = CircleHarmonicMode.from_config(ctx.config)
        cfg = ctx.config
        logger.info(
            f"eddy_seek: circle_harmonic coarse={cfg.sweep_coarse_speed / 60.0:.2f} mm/s "
            f"circle={cfg.circle_speed / 60.0:.2f} mm/s "
            f"arc_res={cfg.circle_arc_resolution} "
            f"refresh_sweeps={mode.refresh_sweeps} "
            f"skip_bootstrap={mode.skip_bootstrap}"
        )

    def on_session_end(self, ctx: SeekSession) -> str | None:
        self._reset()
        return finalize_strategy_plot(ctx, self.name)

    def _reset(self) -> None:
        self._bootstrap = None
        self._x_profile = []
        self._y_profile = []
        self._plateau.reset()

    def search(self, ctx: SeekSession, console: KConsole) -> tuple[Offset, int]:
        cfg = ctx.config
        self._mode = CircleHarmonicMode.from_config(cfg)
        self._plateau.reset()
        best = Offset.zero()
        positions = [best]
        passes_run = 0

        for pass_num in range(1, cfg.max_passes + 1):
            passes_run = pass_num
            logger.info(
                f"eddy_seek: {self.name} pass {pass_num} start "
                f"best=({best.x:.4f}, {best.y:.4f})"
            )
            self._plateau.last_rejected = False
            new = self._step(ctx, pass_num, best)
            moved = (new - best).abs_components()
            console.info(self._pass_message(pass_num, new, moved, ctx))
            positions.append(new)
            if not self._plateau.last_rejected:
                _check_pass_divergence(
                    self.name,
                    positions,
                    tolerance=cfg.tolerance,
                    pass_num=pass_num,
                )
            best = new

            if self._plateau.frozen is not None:
                logger.info(
                    f"eddy_seek: {self.name} finished after pass {pass_num} "
                    f"(frozen at {best.x:.4f}, {best.y:.4f})"
                )
                break

            if moved.x < cfg.tolerance and moved.y < cfg.tolerance:
                if self._plateau.last_rejected:
                    logger.info(
                        f"eddy_seek: {self.name} pass {pass_num} rejected "
                        f"- continuing at smaller circle"
                    )
                    continue
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

    def _sync_mode(self, ctx: SeekSession) -> None:
        self._mode = CircleHarmonicMode.from_config(ctx.config)

    def _step(self, ctx: SeekSession, pass_num: int, best: Offset) -> Offset:
        self._sync_mode(ctx)
        if self._mode.skip_bootstrap:
            if self._bootstrap is None:
                self._bootstrap = best
            return self._circle_pass_step(ctx, pass_num, best)
        if pass_num == 1:
            return bootstrap_pass(self, ctx, pass_num, best)
        return self._circle_pass_step(ctx, pass_num, best)

    def _circle_pass_step(
        self,
        ctx: SeekSession,
        pass_num: int,
        best: Offset,
    ) -> Offset:
        outcome = self._compute_circle_pass(ctx, pass_num, best)
        return self._apply_circle_outcome(ctx, pass_num, best, outcome)

    def _apply_circle_outcome(
        self,
        ctx: SeekSession,
        pass_num: int,
        best: Offset,
        outcome: CirclePassOutcome,
    ) -> Offset:
        bootstrap = self._bootstrap if self._bootstrap is not None else best
        _action, new = self._plateau.advance(
            outcome,
            radius_min=ctx.config.circle_radius_min,
            bootstrap=bootstrap,
        )
        plot = self._plateau.plot_position(outcome, bootstrap=bootstrap)
        if plot is not None:
            self._record_circle_plot(
                ctx,
                pass_num,
                best,
                plot,
                outcome.trace_center,
                outcome.trace_radius,
                outcome.samples,
                outcome.binned,
                fit=outcome.fit,
                rejected=outcome.rejected,
                reject_reasons=outcome.reject_reasons,
            )
        return new

    def _finish_circle_pass(
        self,
        ctx: SeekSession,
        pass_num: int,
        best: Offset,
        outcome: CirclePassOutcome,
    ) -> Offset:
        return self._apply_circle_outcome(ctx, pass_num, best, outcome)

    def _pass_message(
        self,
        pass_num: int,
        new: Offset,
        moved: Offset,
        ctx: SeekSession,
    ) -> str:
        label = (
            "bootstrap" if pass_num == 1 and not self._mode.skip_bootstrap else "circle"
        )
        return f"Pass {pass_num} ({label}): {new.to_console_str()}"

    def _bootstrap_pass(
        self,
        ctx: SeekSession,
        pass_num: int,
        best: Offset,
    ) -> Offset:
        return bootstrap_pass(self, ctx, pass_num, best)

    def _compute_circle_pass(
        self,
        ctx: SeekSession,
        pass_num: int,
        best: Offset,
    ) -> CirclePassOutcome:
        self._sync_mode(ctx)
        return compute_circle_pass(self, ctx, pass_num, best, self._mode, self._plateau)

    def _refresh_profiles(
        self,
        ctx: SeekSession,
        pass_num: int,
        center: Offset,
        radius: float,
    ) -> None:
        from .circle_pass import refresh_profiles

        refresh_profiles(self, ctx, pass_num, center, radius)

    def _record_bootstrap_plot(
        self,
        ctx: SeekSession,
        pass_num: int,
        center: Offset,
        result: Offset,
        samples: list[MotionSample],
        box: tuple[float, float, float, float],
    ) -> None:
        ctx.recorder.record(
            CircleBootstrapRecord(
                pass_num=pass_num,
                move=PassMove.compute(center, result),
                samples=XYCloud.from_samples(samples),
                bounds=Bounds.from_box(box),
            )
        )

    def _record_circle_plot(
        self,
        ctx: SeekSession,
        pass_num: int,
        best: Offset,
        result: Offset,
        trace_center: Offset,
        trace_radius: float,
        samples: list[MotionSample],
        binned: list[tuple[float, float]],
        *,
        fit: HarmonicFit | None,
        rejected: bool,
        reject_reasons: str = "",
    ) -> None:
        ctx.recorder.record(
            CircleHarmonicPassRecord(
                pass_num=pass_num,
                trace_center=trace_center,
                radius=trace_radius,
                move=PassMove.compute(best, result),
                samples=XYCloud.from_samples(samples),
                binned=BinnedProfile(
                    tuple(theta for theta, _ in binned),
                    tuple(freq for _, freq in binned),
                ),
                fit=fit,
                rejected=rejected,
                reject_reasons=reject_reasons,
            )
        )
