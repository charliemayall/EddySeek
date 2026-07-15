"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from typing import Any, cast

from fakes import CommandError, FakeGcmd, RecordingToolhead
from pytest import raises

from eddy_seek.movement.guard import assert_sensor_z


def test_assert_sensor_z_skips_when_unset():
    toolhead = RecordingToolhead()
    toolhead.pos[2] = 99.0
    assert_sensor_z(cast(Any, toolhead), None, FakeGcmd())


def test_assert_sensor_z_passes_at_exact_height():
    toolhead = RecordingToolhead()
    toolhead.pos[2] = 5.0
    assert_sensor_z(cast(Any, toolhead), 5.0, FakeGcmd())


def test_assert_sensor_z_passes_at_upper_band():
    toolhead = RecordingToolhead()
    toolhead.pos[2] = 5.25
    assert_sensor_z(cast(Any, toolhead), 5.0, FakeGcmd())


def test_assert_sensor_z_raises_when_too_low():
    toolhead = RecordingToolhead()
    toolhead.pos[2] = 4.999
    gcmd = FakeGcmd()
    with raises(CommandError, match="Sensor Z guard") as exc_info:
        assert_sensor_z(cast(Any, toolhead), 5.0, gcmd)
    assert "5.000" in str(exc_info.value)
    assert "4.999" in str(exc_info.value)


def test_assert_sensor_z_raises_when_too_high():
    toolhead = RecordingToolhead()
    toolhead.pos[2] = 5.251
    gcmd = FakeGcmd()
    with raises(CommandError, match="Sensor Z guard") as exc_info:
        assert_sensor_z(cast(Any, toolhead), 5.0, gcmd)
    assert "5.251" in str(exc_info.value)
