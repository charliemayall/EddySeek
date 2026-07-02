"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

from _eddy_seek.motion_guard import MotionGuard, clear_gcode_offset_xy


class _FakeToolhead:
    square_corner_velocity = 5.0
    min_cruise_ratio = 0.5
    calc_calls = 0

    def _calc_junction_deviation(self) -> None:
        _FakeToolhead.calc_calls += 1


class _FakeInputShaper:
    disable_calls = 0
    enable_calls = 0

    def disable_shaping(self) -> None:
        _FakeInputShaper.disable_calls += 1

    def enable_shaping(self) -> None:
        _FakeInputShaper.enable_calls += 1


class _FakeGcmd:
    messages: list[str] = []

    def respond_info(self, msg: str) -> None:
        _FakeGcmd.messages.append(msg)


class _FakeGcode:
    scripts: list[str] = []

    def run_script_from_command(self, script: str) -> None:
        _FakeGcode.scripts.append(script)


class _FakeGcodeMove:
    homing_position = [1.5, -0.5, 0.0, 0.0]


class _FakePrinter:
    toolhead = _FakeToolhead()
    input_shaper = _FakeInputShaper()

    def lookup_object(self, name: str, default=None):
        if name == "toolhead":
            return self.toolhead
        if name == "input_shaper":
            return self.input_shaper
        if name == "gcode":
            return _FakeGcode()
        if name == "gcode_move":
            return _FakeGcodeMove()
        return default


def test_clear_gcode_offset_xy_zeros_xy():
    _FakeGcode.scripts = []
    printer = _FakePrinter()
    clear_gcode_offset_xy(printer)  # type: ignore[arg-type]
    assert _FakeGcode.scripts == ["SET_GCODE_OFFSET X=0.000000 Y=0.000000"]


def test_seek_motion_guard_restores_gcode_offset():
    _FakeGcmd.messages = []
    _FakeGcode.scripts = []
    _FakeGcodeMove.homing_position = [1.5, -0.5, 0.0, 0.0]
    gcmd = _FakeGcmd()

    with MotionGuard(_FakePrinter(), gcmd):
        assert _FakeGcode.scripts == ["SET_GCODE_OFFSET X=0.000000 Y=0.000000"]

    assert _FakeGcode.scripts[-1] == "SET_GCODE_OFFSET X=1.500000 Y=-0.500000"
    assert _FakeGcmd.messages == [
        "EDDY_SEEK: cleared gcode offset",
        "EDDY_SEEK: restored motion settings",
    ]


def test_seek_motion_guard_caps_scv_and_restores_toolhead_limits():
    _FakeToolhead.calc_calls = 0
    _FakeGcmd.messages = []
    _FakeGcode.scripts = []
    printer = _FakePrinter()
    toolhead = printer.toolhead
    toolhead.square_corner_velocity = 15.0
    toolhead.min_cruise_ratio = 0.5
    gcmd = _FakeGcmd()

    with MotionGuard(printer, gcmd):
        assert toolhead.square_corner_velocity == 9.0
        assert toolhead.min_cruise_ratio == 0.0
        assert _FakeToolhead.calc_calls == 1

    assert toolhead.square_corner_velocity == 15.0
    assert toolhead.min_cruise_ratio == 0.5
    assert _FakeToolhead.calc_calls == 2


def test_seek_motion_guard_leaves_scv_unchanged_when_below_cap():
    _FakeToolhead.calc_calls = 0
    printer = _FakePrinter()
    toolhead = printer.toolhead
    toolhead.square_corner_velocity = 5.0
    toolhead.min_cruise_ratio = 0.5

    with MotionGuard(printer, None):
        assert toolhead.square_corner_velocity == 5.0
        assert toolhead.min_cruise_ratio == 0.0

    assert toolhead.square_corner_velocity == 5.0
    assert toolhead.min_cruise_ratio == 0.5


def test_seek_motion_guard_disables_and_enables_input_shaper():
    _FakeInputShaper.disable_calls = 0
    _FakeInputShaper.enable_calls = 0
    printer = _FakePrinter()

    with MotionGuard(printer, None):
        assert _FakeInputShaper.disable_calls == 1
        assert _FakeInputShaper.enable_calls == 0

    assert _FakeInputShaper.disable_calls == 1
    assert _FakeInputShaper.enable_calls == 1
