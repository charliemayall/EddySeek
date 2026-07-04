"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from unittest.mock import MagicMock, patch

from fakes import fake_motion_printer
from pytest import raises

from _eddy_seek.common import Axis, Offset, Phase, Position, samples_in_box, search_box
from _eddy_seek.movement.handler import (
    MotionHandler,
    MotionSample,
    align_measurements,
    axis_profile,
    get_clamped_speed_for_min_samples_over_span,
)
from _eddy_seek.movement.leg_planner import (
    iter_cross_offsets,
    sweep_axis,
    traversal_endpoints,
)


def _make_handler(printer, origin: Position = Position.zero()) -> MotionHandler:
    host = MagicMock()
    config = MagicMock()
    config.jog_speed = 3000.0
    config.dwell_time = 0.5
    config.min_sweep_samples = 1
    return MotionHandler(printer, host, config, origin)


def test_iter_cross_offsets_three_passes():
    assert iter_cross_offsets(3, 0.3) == [0.0, 0.3, -0.3]


def test_traversal_endpoints_plus():
    start, end = traversal_endpoints(
        Axis.X, -2.0, 2.0, cross=0.5, overscan=1.0, reverse=False
    )
    assert start == Offset(-3.0, 0.5)
    assert end == Offset(3.0, 0.5)


def test_traversal_endpoints_minus():
    start, end = traversal_endpoints(
        Axis.Y, 0.0, 4.0, cross=-1.0, overscan=0.5, reverse=True
    )
    assert start == Offset(-1.0, 4.5)
    assert end == Offset(-1.0, -0.5)


def test_move_to_absolute():
    printer, toolhead = fake_motion_printer()
    handler = _make_handler(printer, Position(10.0, 20.0))
    handler.move_to(Position(12.0, 22.0))

    toolhead.manual_move.assert_called_once_with([12.0, 22.0], 50.0)
    assert handler.position == Offset(2.0, 2.0)


def test_jog_waits_for_move():
    printer, toolhead = fake_motion_printer()
    handler = _make_handler(printer)
    handler.jog(Offset(1.0, 2.0))

    toolhead.manual_move.assert_called_once()
    toolhead.wait_moves.assert_called_once()


def test_capture_leg_registers_sample_window():
    toolhead = MagicMock()
    toolhead.get_last_move_time.return_value = 1.0
    callbacks: list = []
    toolhead.register_lookahead_callback.side_effect = callbacks.append
    printer, _toolhead = fake_motion_printer(toolhead)

    handler = _make_handler(printer)
    handler.begin(Position(10.0, 20.0))
    handler.capture_leg(Offset(0.0, 0.0), Offset(1.0, 0.0), 2400.0)

    assert toolhead.manual_move.call_args_list == [
        (([10.0, 20.0], 40.0),),
        (([11.0, 20.0], 40.0),),
    ]
    assert len(callbacks) == 1
    callbacks[0](2.0)
    assert handler._capture_windows == [(1.0, 2.0)]
    assert handler.position == Offset(1.0, 0.0)


def test_align_measurements_uses_toolhead_lookup():
    toolhead = MagicMock()
    toolhead.get_kinematics.return_value.get_steppers.return_value = []
    toolhead.get_kinematics.return_value.calc_position.return_value = [10.5, 20.0]

    with patch(
        "_eddy_seek.movement.handler.lookup_toolhead_position",
        return_value=Position(10.5, 20.0),
    ) as lookup:
        samples = align_measurements(toolhead, Position(10.0, 20.0), [(1.0, 100.0)])

    lookup.assert_called_once_with(toolhead, 1.0)
    assert samples == [MotionSample(Offset(0.5, 0.0), 100.0, 1.0)]


def test_capture_leg_requires_active_session():
    handler = _make_handler(MagicMock())
    with raises(RuntimeError, match="not active"):
        handler.capture_leg(Offset.zero(), Offset(1.0, 0.0), 2400.0)


def test_axis_profile_filters_sweep_range():
    samples = [
        MotionSample(Offset(-3.0, 0.0), 1.0, 0.0),
        MotionSample(Offset(-1.0, 0.0), 2.0, 0.1),
        MotionSample(Offset(0.0, 0.0), 3.0, 0.2),
        MotionSample(Offset(2.0, 0.0), 4.0, 0.3),
    ]
    points = axis_profile(samples, Axis.X, lo=-2.0, hi=2.0)
    assert points == [(-1.0, 2.0), (0.0, 3.0), (2.0, 4.0)]


def test_samples_in_box_filters_xy():
    box = search_box(Offset(0.0, 0.0), 1.0, 1.0, 5.0, 5.0)
    samples = [
        MotionSample(Offset(-0.5, 0.0), 1.0, 0.0),
        MotionSample(Offset(2.0, 0.0), 2.0, 0.1),
        MotionSample(Offset(0.0, -0.5), 3.0, 0.2),
    ]
    in_box = samples_in_box(samples, box)
    assert len(in_box) == 2
    assert in_box[0].offset == Offset(-0.5, 0.0)
    assert in_box[1].offset == Offset(0.0, -0.5)


def test_speed_clamp_for_min_samples_caps_when_too_fast():
    cap = get_clamped_speed_for_min_samples_over_span(
        requested_mm_min=3000.0,
        span_mm=2.0,
        min_samples=20,
    )
    assert cap == 2400.0


def test_speed_clamp_for_min_samples_leaves_slow_request():
    assert (
        get_clamped_speed_for_min_samples_over_span(
            requested_mm_min=1200.0,
            span_mm=2.0,
            min_samples=20,
        )
        == 1200.0
    )


def test_sweep_axis_passes_speed_through():
    """Speed clamping is the caller's job; sweep_axis must not alter it."""
    ctx = MagicMock()
    ctx.config.min_sweep_samples = 20
    ctx.config.sweep_overscan = 1.0
    ctx.session_start = Position.zero()
    handler = MagicMock()
    ctx.motion = handler
    handler.collect_samples.return_value = []

    speed = 1800.0
    sweep_axis(ctx, Axis.X, 0.5, 1.5, 0.0, [0.0], speed, Phase.FINE, 2)

    handler.run_capture_legs.assert_called_once()
    assert handler.run_capture_legs.call_args.args[1] == speed
