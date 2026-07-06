"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from unittest.mock import MagicMock, patch

from fakes import fake_motion_printer
from pytest import raises

from _eddy_seek.common import Axis, Offset, Phase, Position, samples_in_box, search_box
from _eddy_seek.config import SeekConfig
from _eddy_seek.movement.handler import (
    MotionHandler,
    MotionSample,
    align_measurements,
    axis_profile,
    get_clamped_speed_for_min_samples_over_span,
)
from _eddy_seek.movement.leg_planner import (
    MotionCapture,
    SweepSettings,
    axis_sweep_centroid,
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


def _capture(
    handler: MotionHandler, origin: Position = Position.zero()
) -> MotionCapture:
    return MotionCapture(handler, origin)


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


def test_sweep_axis_clamps_speed_for_min_samples():
    settings = SweepSettings.from_config(
        SeekConfig(
            max_jog_x=2.5,
            max_jog_y=2.5,
            min_sweep_samples=20,
            cross_passes=1,
        )
    )
    capture = MagicMock()
    capture.collect_legs.return_value = [
        MotionSample(Offset(-0.1 + i * 0.01, 0.0), 100.0, 0.0) for i in range(21)
    ]

    sweep_axis(
        capture,
        settings,
        axis=Axis.X,
        lo=-0.1,
        hi=0.1,
        cross_center=0.0,
        speed_mm_min=3000.0,
        phase=Phase.COARSE,
        pass_num=1,
    )

    # span=0.2 mm -> cap = 0.2 * 400 Hz * 60 / 20 = 240 mm/min
    assert capture.collect_legs.call_args.args[1] == 240.0


def test_sweep_axis_coarse_uses_settings_cross_passes():
    settings = SweepSettings.from_config(
        SeekConfig(cross_passes=1, min_sweep_samples=3)
    )
    capture = MagicMock()
    capture.collect_legs.return_value = [
        MotionSample(Offset(i * 0.01, 0.0), 100.0, 0.0) for i in range(3)
    ]

    with patch(
        "_eddy_seek.movement.leg_planner.plan_axis_legs",
        return_value=[],
    ) as plan_legs:
        sweep_axis(
            capture,
            settings,
            axis=Axis.X,
            lo=-1.0,
            hi=1.0,
            cross_center=0.0,
            speed_mm_min=600.0,
            phase=Phase.COARSE,
            pass_num=1,
        )

    assert plan_legs.call_args.args[4] == [0.0]


def test_sweep_axis_fine_phase_uses_single_cross_pass():
    settings = SweepSettings.from_config(
        SeekConfig(cross_passes=3, min_sweep_samples=3)
    )
    capture = MagicMock()
    capture.collect_legs.return_value = [
        MotionSample(Offset(i * 0.01, 0.0), 100.0, 0.0) for i in range(3)
    ]

    with patch(
        "_eddy_seek.movement.leg_planner.plan_axis_legs",
        return_value=[],
    ) as plan_legs:
        sweep_axis(
            capture,
            settings,
            axis=Axis.X,
            lo=-1.0,
            hi=1.0,
            cross_center=0.0,
            speed_mm_min=600.0,
            phase=Phase.FINE,
            pass_num=3,
        )

    assert plan_legs.call_args.args[4] == [0.0]


def test_axis_sweep_centroid_builds_profiles_and_centroid():
    settings = SweepSettings.from_config(
        SeekConfig(search_for="max", min_sweep_samples=5)
    )
    capture = MagicMock()

    samples_x = [
        MotionSample(Offset(x, 0.0), 100.0 - 5.0 * x * x, 0.0)
        for x in [i * 0.1 for i in range(-10, 11)]
    ]
    samples_y = [
        MotionSample(Offset(0.0, y), 100.0 - 5.0 * y * y, 0.0)
        for y in [i * 0.1 for i in range(-10, 11)]
    ]

    with patch(
        "_eddy_seek.movement.leg_planner.sweep_axis",
        side_effect=[samples_x, samples_y],
    ):
        result = axis_sweep_centroid(
            capture,
            settings,
            Offset.zero(),
            half_x=5.0,
            half_y=5.0,
            speed_mm_min=3000.0,
            phase=Phase.COARSE,
            pass_num=1,
            label="test",
        )

    assert len(result.in_box) == len(samples_x) + len(samples_y)
    assert len(result.x_profile) == len(samples_x)
    assert len(result.y_profile) == len(samples_y)
    assert result.centroid is not None
    assert result.centroid.x == 0.0
    assert result.centroid.y == 0.0


def test_axis_sweep_centroid_raises_when_too_few_samples():
    settings = SweepSettings.from_config(SeekConfig(min_sweep_samples=50))
    capture = MagicMock()

    samples_x = [
        MotionSample(Offset(x, 0.0), 100.0, 0.0) for x in [i * 0.1 for i in range(5)]
    ]
    samples_y = [
        MotionSample(Offset(0.0, y), 100.0, 0.0) for y in [i * 0.1 for i in range(5)]
    ]

    with (
        patch(
            "_eddy_seek.movement.leg_planner.sweep_axis",
            side_effect=[samples_x, samples_y],
        ),
        raises(RuntimeError, match="test collected 10 in-range samples"),
    ):
        axis_sweep_centroid(
            capture,
            settings,
            Offset.zero(),
            half_x=5.0,
            half_y=5.0,
            speed_mm_min=3000.0,
            phase=Phase.COARSE,
            pass_num=1,
            label="test",
        )
