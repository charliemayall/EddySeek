"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Leg geometry, sweep/grid path planning, and session capture orchestration.
"""

from __future__ import annotations

import logging
import math
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
    cross_passes: int
    sweep_cross_offset: float
    min_sweep_samples: int
    search_for: Literal["min", "max"]

    @classmethod
    def from_config(cls, cfg: SeekConfig) -> SweepSettings:
        return cls(**{f.name: getattr(cfg, f.name) for f in fields(cls)})


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
    ) -> list[MotionSample] | list[list[MotionSample]]:
        if min_samples is not None:
            speed_mm_min = get_clamped_speed_for_min_samples_over_span(
                requested_mm_min=speed_mm_min,
                span_mm=span_mm if span_mm is not None else _min_leg_span_mm(legs),
                min_samples=min_samples,
            )
        self.handler.begin(self.origin)
        self.handler.run_capture_legs(legs, speed_mm_min)
        if self.sync_offset is not None:
            self.sync_offset(self.handler.position)
        if flat:
            return list(self.handler.collect_samples(flat=True))
        return [list(batch) for batch in self.handler.collect_samples(flat=False)]


def _min_leg_span_mm(legs: Sequence[tuple[Offset, Offset]]) -> float:
    if not legs:
        return 0.0
    return min(math.hypot(end.x - start.x, end.y - start.y) for start, end in legs)


def effective_overscan(overscan: float) -> float:
    return min(overscan, 8.0) if overscan > 0.0 else 0.0


def traversal_endpoints(
    axis: Axis,
    lo: float,
    hi: float,
    cross: float,
    overscan: float,
    *,
    reverse: bool = False,
) -> tuple[Offset, Offset]:
    """Session offsets for one continuous sweep traverse (includes overscan)."""
    if hi < lo:
        lo, hi = hi, lo
    ovs = effective_overscan(overscan)
    if not reverse:
        return (
            Offset.from_axis(axis, lo - ovs, cross),
            Offset.from_axis(axis, hi + ovs, cross),
        )
    return (
        Offset.from_axis(axis, hi + ovs, cross),
        Offset.from_axis(axis, lo - ovs, cross),
    )


def iter_cross_offsets(passes: int, offset: float) -> list[float]:
    """Staggered perpendicular offsets: 0, +offset, -offset, …"""
    if passes <= 1:
        return [0.0]
    result = [0.0]
    for i in range(1, passes):
        if i % 2 == 1:
            result.append(offset * ((i + 1) // 2))
        else:
            result.append(-offset * (i // 2))
    return result[:passes]


def y_lines(y_lo: float, y_hi: float, tolerance: float) -> list[float]:
    """Cross-axis coordinates for raster rows, spaced by ``tolerance`` (inclusive)."""
    if tolerance <= 0.0:
        raise ValueError("tolerance must be positive")
    if y_hi < y_lo:
        y_lo, y_hi = y_hi, y_lo
    count = int((y_hi - y_lo) / tolerance) + 1
    return [y_lo + index * tolerance for index in range(count)]


def plan_axis_legs(
    axis: Axis,
    lo: float,
    hi: float,
    cross_center: float,
    cross_offsets: list[float],
    overscan: float,
) -> list[tuple[Offset, Offset]]:
    """Parallel traverses on ``axis`` at staggered cross-axis offsets."""
    legs: list[tuple[Offset, Offset]] = []
    for cross_delta in cross_offsets:
        cross = cross_center + cross_delta
        # for reverse in (False, True):
        legs.extend(
            [
                traversal_endpoints(axis, lo, hi, cross, overscan, reverse=reverse)
                for reverse in (False, True)
            ]
        )

    return legs


def plan_grid_legs(
    box: tuple[float, float, float, float],
    tolerance: float,
    overscan: float,
    *,
    axis: Axis = Axis.X,
    serpentine: bool = True,
) -> list[tuple[Offset, Offset]]:
    """Raster sweeps on ``axis`` at lines spaced by ``tolerance`` on the cross axis."""
    x_lo, x_hi, y_lo, y_hi = box
    if axis is Axis.X:
        lo, hi, cross_lo, cross_hi = x_lo, x_hi, y_lo, y_hi
    else:
        lo, hi, cross_lo, cross_hi = y_lo, y_hi, x_lo, x_hi
    legs: list[tuple[Offset, Offset]] = []
    for line_index, cross in enumerate(y_lines(cross_lo, cross_hi, tolerance)):
        if serpentine and line_index % 2 == 1:
            traverses = (True, False)
        else:
            traverses = (False, True)
        legs.extend(
            [
                traversal_endpoints(axis, lo, hi, cross, overscan, reverse=reverse)
                for reverse in traverses
            ]
        )

    return legs


def _resolve_cross(settings: SweepSettings, phase: Phase) -> list[float]:
    passes = 1 if phase is Phase.FINE else settings.cross_passes
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


def _assert_grid_leg_count() -> None:
    box = (-5.0, 5.0, -5.0, 5.0)
    tolerance = 0.1
    rows = len(y_lines(box[2], box[3], tolerance))
    legs = plan_grid_legs(box, tolerance, overscan=1.0)
    assert len(legs) == rows * 2


_assert_grid_leg_count()
