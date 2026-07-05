"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Leg geometry, sweep/grid path planning, and session capture orchestration.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from ..common import Axis, Offset, Phase, samples_in_box, search_box
from ..optimizer import decoupled_centroid
from ..plotting.primitives import (
    AxisSpan,
    Bounds,
    SweepGridTraceRecord,
    SweepTraceRecord,
)
from ..session import SeekSession
from .handler import (
    MotionSample,
    axis_profile,
    get_clamped_speed_for_min_samples_over_span,
)

logger = logging.getLogger(__name__)


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
        for reverse in (False, True):
            legs.append(
                traversal_endpoints(axis, lo, hi, cross, overscan, reverse=reverse)
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
        for reverse in traverses:
            legs.append(
                traversal_endpoints(axis, lo, hi, cross, overscan, reverse=reverse)
            )
    return legs


def get_samples_from_capture_legs(
    ctx: SeekSession,
    legs: Sequence[tuple[Offset, Offset]],
    speed: float,
) -> list[MotionSample]:
    """Run continuous capture legs and return merged session-relative samples."""
    handler = ctx.motion
    handler.begin(ctx.session_start)
    handler.run_capture_legs(legs, speed)
    ctx.sync_offset(handler.position)
    return handler.collect_samples()


def sweep_axis(
    ctx: SeekSession,
    axis: Axis,
    lo: float,
    hi: float,
    cross_center: float,
    cross_offsets: list[float],
    speed: float,
    phase: Phase,
    pass_num: int,
) -> tuple[list[tuple[float, float]], list[MotionSample]]:
    """Continuous ± traverses on ``axis``; merged profile in session offsets."""
    cfg = ctx.config
    if hi < lo:
        lo, hi = hi, lo
    legs = plan_axis_legs(axis, lo, hi, cross_center, cross_offsets, cfg.sweep_overscan)
    samples = get_samples_from_capture_legs(ctx, legs, speed)
    points = axis_profile(samples, axis, lo, hi)

    logger.info(
        f"eddy_seek: sweep_axis {axis.value} pass {pass_num} {phase.value} "
        f"cross_passes={len(cross_offsets)} -> {len(points)} points"
    )
    if ctx.recorder.trace:
        ctx.recorder.record(
            SweepTraceRecord(
                pass_num=pass_num,
                phase=phase.value,
                span=AxisSpan(axis, lo, hi),
                cross_offsets=tuple(cross_offsets),
                cross_center=cross_center,
                profile=tuple(points),
            )
        )
    return points, samples


def clamped_sweep_axis(
    ctx: SeekSession,
    axis: Axis,
    center: float,
    half_range: float,
    cross_center: float,
    speed: float,
    phase: Phase,
    pass_num: int,
) -> tuple[list[tuple[float, float]], list[MotionSample]]:
    """Sweep one axis within jog limits, with speed clamped for min sample count."""
    cfg = ctx.config
    jog_limit = cfg.max_jog_x if axis is Axis.X else cfg.max_jog_y
    lo = max(-jog_limit, center - half_range)
    hi = min(jog_limit, center + half_range)
    cross_offsets = iter_cross_offsets(cfg.sweep_cross_passes, cfg.sweep_cross_offset)
    clamped_speed = get_clamped_speed_for_min_samples_over_span(
        requested_mm_min=speed,
        span_mm=abs(hi - lo),
        min_samples=cfg.min_sweep_samples,
    )
    points, samples = sweep_axis(
        ctx,
        axis=axis,
        lo=lo,
        hi=hi,
        cross_center=cross_center,
        cross_offsets=cross_offsets,
        speed=clamped_speed,
        phase=phase,
        pass_num=pass_num,
    )
    if len(points) < cfg.min_sweep_samples:
        raise RuntimeError(
            f"eddy_seek: sweep on {axis.value} collected {len(points)} samples "
            f"(need >= {cfg.min_sweep_samples})"
        )
    return points, samples


@dataclass(frozen=True, slots=True)
class AxisSweepCentroidResult:
    box: tuple[float, float, float, float]
    in_box_x: list[MotionSample]
    in_box_y: list[MotionSample]
    x_profile: list[tuple[float, float]]
    y_profile: list[tuple[float, float]]
    centroid: Offset | None

    @property
    def in_box(self) -> list[MotionSample]:
        return [*self.in_box_x, *self.in_box_y]


def axis_sweep_centroid(
    ctx: SeekSession,
    center: Offset,
    *,
    half_x: float,
    half_y: float,
    speed: float,
    phase: Phase,
    pass_num: int,
    label: str,
) -> AxisSweepCentroidResult:
    """X/Y sweeps, box filter, and decoupled centroid from axis profiles."""
    cfg = ctx.config
    _, samples_x = clamped_sweep_axis(
        ctx, Axis.X, center.x, half_x, center.y, speed, phase, pass_num
    )
    _, samples_y = clamped_sweep_axis(
        ctx, Axis.Y, center.y, half_y, center.x, speed, phase, pass_num
    )
    box = search_box(center, half_x, half_y, cfg.max_jog_x, cfg.max_jog_y)
    in_box_x = samples_in_box(samples_x, box)
    in_box_y = samples_in_box(samples_y, box)
    in_box = [*in_box_x, *in_box_y]

    if len(in_box) < cfg.min_sweep_samples:
        raise RuntimeError(
            f"eddy_seek: {label} collected {len(in_box)} in-range samples "
            f"(need >= {cfg.min_sweep_samples}). "
            "Check sensor and sweep speed."
        )

    x_profile = [(sample.offset.x, sample.freq) for sample in in_box_x]
    y_profile = [(sample.offset.y, sample.freq) for sample in in_box_y]
    centroid = decoupled_centroid(x_profile, y_profile, cfg.search_for)
    return AxisSweepCentroidResult(
        box=box,
        in_box_x=in_box_x,
        in_box_y=in_box_y,
        x_profile=x_profile,
        y_profile=y_profile,
        centroid=centroid,
    )


def sweep_grid(
    ctx: SeekSession,
    center: Offset,
    speed: float,
    step_size: float,
) -> tuple[list[MotionSample], tuple[float, float, float, float]]:
    """Raster the search box once; return samples clipped to the box bounds."""
    cfg = ctx.config
    box = search_box(center, cfg.max_jog_x, cfg.max_jog_y, cfg.max_jog_x, cfg.max_jog_y)
    legs = plan_grid_legs(box, step_size, cfg.sweep_overscan, axis=Axis.X)
    legs.extend(plan_grid_legs(box, step_size, cfg.sweep_overscan, axis=Axis.Y))
    _, _, y_lo, y_hi = box
    samples = get_samples_from_capture_legs(ctx, legs, speed)
    in_box = samples_in_box(samples, box)
    rows = len(y_lines(y_lo, y_hi, step_size))
    logger.info(
        f"eddy_seek: sweep_grid rows={rows} legs={len(legs)} "
        f"samples={len(samples)} in_box={len(in_box)}"
    )
    if ctx.recorder.trace:
        ctx.recorder.record(
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
