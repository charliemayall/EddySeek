"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from _eddy_seek.common import Axis, Offset
from _eddy_seek.movement.paths import (
    cross_pass_connector_legs,
    cubic_bezier_chord_legs,
    plan_axis_leg_connectors,
    plan_axis_legs,
    uturn_connector_legs,
)


def test_cross_pass_connector_endpoints():
    from_pt = Offset(-3.0, 0.0)
    to_pt = Offset(-3.0, 0.3)
    legs = cross_pass_connector_legs(
        Axis.X, from_pt, to_pt, lead_mm=0.3, resolution=0.1
    )
    assert len(legs) >= 3
    assert legs[0][0] == from_pt
    assert legs[-1][1] == to_pt
    max_x = max(end.x for _, end in legs)
    assert max_x <= -3.0 + 0.3 + 1e-9


def test_uturn_connector_loops_at_pivot():
    pivot = Offset(3.0, 0.0)
    legs = uturn_connector_legs(
        Axis.X, pivot, lead_mm=0.3, bulge_mm=0.3, resolution=0.1
    )
    assert len(legs) >= 3
    assert legs[0][0] == pivot
    assert legs[-1][1] == pivot
    mid = legs[len(legs) // 2][1]
    assert mid.y > pivot.y


def test_plan_axis_leg_connectors_three_cross_offsets():
    legs = plan_axis_legs(
        Axis.X,
        -2.0,
        2.0,
        cross_center=0.0,
        cross_offsets=[0.0, 0.3, -0.3],
        overscan=1.0,
    )
    connectors = plan_axis_leg_connectors(
        legs,
        Axis.X,
        overscan=1.0,
        cross_offset=0.3,
        resolution=0.1,
    )
    assert len(connectors) == len(legs) - 1
    assert all(connector is not None for connector in connectors)
    assert len(connectors) == 5


def test_plan_axis_leg_connectors_single_cross_uturn_only():
    legs = plan_axis_legs(
        Axis.X, -2.0, 2.0, cross_center=0.0, cross_offsets=[0.0], overscan=1.0
    )
    connectors = plan_axis_leg_connectors(
        legs,
        Axis.X,
        overscan=1.0,
        cross_offset=0.3,
        resolution=0.1,
    )
    assert len(connectors) == 1
    assert connectors[0] is not None


def test_cubic_bezier_chord_legs_minimum_segments():
    p0 = Offset(0.0, 0.0)
    p3 = Offset(1.0, 0.0)
    legs = cubic_bezier_chord_legs(p0, p0, p3, p3, resolution=10.0)
    assert len(legs) == 3
