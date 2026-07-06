"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Sweep capture orchestration: settings, motion binding, axis/grid sweeps.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Literal, overload

from ..common import Axis, Offset, Phase, Position, samples_in_box, search_box
from ..optimizer import decoupled_centroid
from ..plotting.primitives import (
    AxisSpan,
    Bounds,
    SweepGridTraceRecord,
    SweepTraceRecord,
)
from .handler import (
    MotionHandler,
    MotionSample,
    axis_profile,
    get_clamped_speed_for_min_samples_over_span,
)
from .paths import (
    iter_cross_offsets,
    min_leg_span_mm,
    plan_axis_legs,
    plan_grid_legs,
    y_lines,
)

if TYPE_CHECKING:
    from ..config import SeekConfig
    from ..plotting.recorder import SessionRecorder

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SweepSettings:
    """Sweep releveant parameters extracted from seek config."""

    max_jog_x: float
    max_jog_y: float
    sweep_overscan: float
    coarse_cross_passes: int
    sweep_cross_offset: float
    min_sweep_samples: int
    search_for: Literal["min", "max"]

    @classmethod
    def from_config(
        cls, cfg: SeekConfig, *, coarse_cross_passes: int = 3
    ) -> SweepSettings:
        values = {
            f.name: getattr(cfg, f.name)
            for f in fields(cls)
            if f.name != "coarse_cross_passes"
        }
        values["coarse_cross_passes"] = coarse_cross_passes
        return cls(**values)


@dataclass(frozen=True, slots=True)
class MotionCapture:
    """Continuous-capture binding: handler, session origin, optional offset sync."""

    handler: MotionHandler
    origin: Position
    sync_offset: Callable[[Offset], None] | None = None

    @overload
    def run(
        self,
        legs: Sequence[tuple[Offset, Offset]],
        speed_mm_min: float,
        *,
        flat: Literal[True] = True,
        min_samples: int | None = None,
        span_mm: float | None = None,
    ) -> list[MotionSample]: ...

    @overload
    def run(
        self,
        legs: Sequence[tuple[Offset, Offset]],
        speed_mm_min: float,
        *,
        flat: Literal[False],
        min_samples: int | None = None,
        span_mm: float | None = None,
    ) -> list[list[MotionSample]]: ...

    def run(
        self,
        legs: Sequence[tuple[Offset, Offset]],
        speed_mm_min: float,
        *,
        flat: bool = True,
        min_samples: int | None = None,
        span_mm: float | None = None,
        lead_in_legs: Sequence[tuple[Offset, Offset]] | None = None,
    ) -> list[MotionSample] | list[list[MotionSample]]:
        """
        Run a sequence of legs, returning motion samples.

        Args:
            legs: Sequence of leg endpoints.
            speed_mm_min: Minimum speed in mm/min.
            flat: If True, return a single list of samples.
            min_samples: Minimum number of samples to collect.
            span_mm: Span in mm.
            lead_in_legs: Uncaptured warmup legs before ``legs`` (same session).
        Returns:
            If ``flat`` is True --> list[MotionSample]

            If ``flat`` is False --> list[list[MotionSample]]
        """
        if min_samples is not None:
            speed_mm_min = get_clamped_speed_for_min_samples_over_span(
                requested_mm_min=speed_mm_min,
                span_mm=span_mm if span_mm is not None else min_leg_span_mm(legs),
                min_samples=min_samples,
            )
        self.handler.begin(self.origin)
        self.handler.run_capture_legs(
            legs, speed_mm_min, lead_in_legs=lead_in_legs or None
        )
        if self.sync_offset is not None:
            self.sync_offset(self.handler.position)
        if flat:
            return list(self.handler.collect_samples(flat=True))
        return [list(batch) for batch in self.handler.collect_samples(flat=False)]


def _resolve_cross(settings: SweepSettings, phase: Phase) -> list[float]:
    passes = 1 if phase is Phase.FINE else settings.coarse_cross_passes
    return iter_cross_offsets(passes, settings.sweep_cross_offset)


def _require_min_sweep_samples(count: int, min_samples: int, *, label: str) -> None:
    if count < min_samples:
        raise RuntimeError(
            f"eddy_seek: {label} collected {count} in-range samples "
            f"(need >= {min_samples}). "
            "Check sensor and sweep speed."
        )


def sweep_axis(
    capture: MotionCapture,
    settings: SweepSettings,
    *,
    axis: Axis,
    lo: float,
    hi: float,
    cross_center: float,
    speed_mm_min: float,
    phase: Phase,
    pass_num: int,
    recorder: SessionRecorder | None = None,
) -> list[MotionSample]:
    """Continuous +/- traverses on ``axis``; returns in-span motion samples."""
    if hi < lo:
        lo, hi = hi, lo
    span_mm = abs(hi - lo)
    cross_offsets = _resolve_cross(settings, phase)
    legs = plan_axis_legs(
        axis, lo, hi, cross_center, cross_offsets, settings.sweep_overscan
    )
    samples = capture.run(
        legs,
        speed_mm_min,
        min_samples=settings.min_sweep_samples,
        span_mm=span_mm,
    )
    profile = axis_profile(samples, axis, lo, hi)

    logger.info(
        f"eddy_seek: sweep_axis {axis.value} pass {pass_num} {phase.value} "
        f"cross_passes={len(cross_offsets)} -> {len(profile)} in-span samples"
    )
    if recorder is not None and recorder.trace:
        recorder.record(
            SweepTraceRecord(
                pass_num=pass_num,
                phase=phase.value,
                span=AxisSpan(axis, lo, hi),
                cross_offsets=tuple(cross_offsets),
                cross_center=cross_center,
                profile=tuple(profile),
            )
        )
    return samples


@dataclass(frozen=True, slots=True)
class AxisSweepProfiles:
    box: tuple[float, float, float, float]
    in_box: list[MotionSample]
    x_profile: list[tuple[float, float]]
    y_profile: list[tuple[float, float]]


def axis_sweep_profiles(
    capture: MotionCapture,
    settings: SweepSettings,
    center: Offset,
    *,
    half_x: float,
    half_y: float,
    speed_mm_min: float,
    phase: Phase,
    pass_num: int,
    label: str = "axis sweep",
    recorder: SessionRecorder | None = None,
) -> AxisSweepProfiles:
    """X/Y sweeps with box-filtered axis profiles (no centroid)."""
    lo_x, hi_x, lo_y, hi_y = search_box(
        center, half_x, half_y, settings.max_jog_x, settings.max_jog_y
    )
    sweep_kw = {"recorder": recorder}
    samples_x = sweep_axis(
        capture,
        settings,
        axis=Axis.X,
        lo=lo_x,
        hi=hi_x,
        cross_center=center.y,
        speed_mm_min=speed_mm_min,
        phase=phase,
        pass_num=pass_num,
        **sweep_kw,
    )
    samples_y = sweep_axis(
        capture,
        settings,
        axis=Axis.Y,
        lo=lo_y,
        hi=hi_y,
        cross_center=center.x,
        speed_mm_min=speed_mm_min,
        phase=phase,
        pass_num=pass_num,
        **sweep_kw,
    )
    box = lo_x, hi_x, lo_y, hi_y
    in_box_x = samples_in_box(samples_x, box)
    in_box_y = samples_in_box(samples_y, box)
    in_box = [*in_box_x, *in_box_y]
    _require_min_sweep_samples(len(in_box), settings.min_sweep_samples, label=label)
    return AxisSweepProfiles(
        box=box,
        in_box=in_box,
        x_profile=[(sample.offset.x, sample.freq) for sample in in_box_x],
        y_profile=[(sample.offset.y, sample.freq) for sample in in_box_y],
    )


@dataclass(frozen=True, slots=True)
class AxisSweepCentroidResult:
    box: tuple[float, float, float, float]
    in_box: list[MotionSample]
    x_profile: list[tuple[float, float]]
    y_profile: list[tuple[float, float]]
    centroid: Offset | None


def axis_sweep_centroid(
    capture: MotionCapture,
    settings: SweepSettings,
    center: Offset,
    *,
    half_x: float,
    half_y: float,
    speed_mm_min: float,
    phase: Phase,
    pass_num: int,
    label: str,
    recorder: SessionRecorder | None = None,
) -> AxisSweepCentroidResult:
    """X/Y sweeps, box filter, and decoupled centroid from axis profiles."""
    profiles = axis_sweep_profiles(
        capture,
        settings,
        center,
        half_x=half_x,
        half_y=half_y,
        speed_mm_min=speed_mm_min,
        phase=phase,
        pass_num=pass_num,
        label=label,
        recorder=recorder,
    )
    centroid = decoupled_centroid(
        profiles.x_profile, profiles.y_profile, settings.search_for
    )
    return AxisSweepCentroidResult(
        box=profiles.box,
        in_box=profiles.in_box,
        x_profile=profiles.x_profile,
        y_profile=profiles.y_profile,
        centroid=centroid,
    )


def sweep_grid(
    capture: MotionCapture,
    settings: SweepSettings,
    center: Offset,
    speed_mm_min: float,
    step_size: float,
    *,
    recorder: SessionRecorder | None = None,
) -> tuple[list[MotionSample], tuple[float, float, float, float]]:
    """Raster the search box once; return samples clipped to the box bounds."""
    box = search_box(
        center,
        settings.max_jog_x,
        settings.max_jog_y,
        settings.max_jog_x,
        settings.max_jog_y,
    )
    legs = plan_grid_legs(box, step_size, settings.sweep_overscan, axis=Axis.X)
    legs.extend(plan_grid_legs(box, step_size, settings.sweep_overscan, axis=Axis.Y))
    _, _, y_lo, y_hi = box
    samples = capture.run(legs, speed_mm_min)
    in_box = samples_in_box(samples, box)
    rows = len(y_lines(y_lo, y_hi, step_size))
    logger.info(
        f"eddy_seek: sweep_grid rows={rows} legs={len(legs)} "
        f"samples={len(samples)} in_box={len(in_box)}"
    )
    if recorder is not None and recorder.trace:
        recorder.record(
            SweepGridTraceRecord(
                center=center,
                bounds=Bounds.from_box(box),
                step_size=step_size,
                rows=rows,
                legs=len(legs),
                sample_count=len(in_box),
            )
        )
    return in_box, box
