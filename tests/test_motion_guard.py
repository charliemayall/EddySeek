"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from fakes import FakeGcode, FakePrinter

from _eddy_seek.movement.guard import (
    MAX_ACCEL,
    MAX_SCV,
    KnownKinematicLimits,
    clear_gcode_offset_xy,
)


class _FakeToolhead:
    max_velocity = 200.0
    max_accel = 3000.0
    square_corner_velocity = 5.0
    min_cruise_ratio = 0.5
    calc_calls = 0

    def _calc_junction_deviation(self) -> None:
        _FakeToolhead.calc_calls += 1

    def get_max_velocity(self) -> tuple[float, float]:
        return self.max_velocity, self.max_accel

    def set_max_velocities(
        self,
        max_velocity: float | None,
        max_accel: float | None,
        square_corner_velocity: float | None,
        min_cruise_ratio: float | None,
    ) -> tuple[float, float, float, float]:
        if max_velocity is not None:
            self.max_velocity = max_velocity
        if max_accel is not None:
            self.max_accel = max_accel
        if square_corner_velocity is not None:
            self.square_corner_velocity = square_corner_velocity
        if min_cruise_ratio is not None:
            self.min_cruise_ratio = min_cruise_ratio
        self._calc_junction_deviation()
        return (
            self.max_velocity,
            self.max_accel,
            self.square_corner_velocity,
            self.min_cruise_ratio,
        )


class _FakeInputShaper:
    disable_calls = 0
    enable_calls = 0

    def disable_shaping(self) -> None:
        _FakeInputShaper.disable_calls += 1

    def enable_shaping(self) -> None:
        _FakeInputShaper.enable_calls += 1


class _FakeGcodeMove:
    def __init__(self) -> None:
        self.homing_position = [1.5, -0.5, 0.0, 0.0]


def _known_state_printer() -> FakePrinter:
    return FakePrinter(
        toolhead=_FakeToolhead(),
        input_shaper=_FakeInputShaper(),
        gcode=FakeGcode(),
        gcode_move=_FakeGcodeMove(),
    )


def test_clear_gcode_offset_xy_zeros_xy():
    printer = FakePrinter(gcode=FakeGcode())
    clear_gcode_offset_xy(printer)  # pyright: ignore[reportArgumentType]
    assert printer.gcode.scripts == ["SET_GCODE_OFFSET X=0.0 Y=0.0"]


def test_known_kinematic_limits_does_not_touch_gcode_offset():
    gcode = FakeGcode()
    printer = FakePrinter(gcode=gcode, toolhead=_FakeToolhead())

    with KnownKinematicLimits(printer):  # pyright: ignore[reportArgumentType]
        pass

    assert gcode.scripts == []


def test_known_kinematic_limits_caps_scv_and_restores_toolhead_limits():
    _FakeToolhead.calc_calls = 0
    printer = _known_state_printer()
    toolhead = printer.lookup_object("toolhead")
    toolhead.square_corner_velocity = 15.0
    toolhead.min_cruise_ratio = 0.5

    with KnownKinematicLimits(printer):  # pyright: ignore[reportArgumentType]
        assert toolhead.square_corner_velocity == MAX_SCV
        assert toolhead.min_cruise_ratio == 0.5
        assert _FakeToolhead.calc_calls == 1

    assert toolhead.square_corner_velocity == 15.0
    assert toolhead.min_cruise_ratio == 0.5
    assert _FakeToolhead.calc_calls == 2


def test_known_kinematic_limits_leaves_scv_unchanged_when_below_cap():
    _FakeToolhead.calc_calls = 0
    printer = _known_state_printer()
    toolhead = printer.lookup_object("toolhead")
    toolhead.square_corner_velocity = 5.0
    toolhead.min_cruise_ratio = 0.5

    with KnownKinematicLimits(printer):  # pyright: ignore[reportArgumentType]
        assert toolhead.square_corner_velocity == 5.0
        assert toolhead.min_cruise_ratio == 0.5

    assert toolhead.square_corner_velocity == 5.0
    assert toolhead.min_cruise_ratio == 0.5


def test_known_kinematic_limits_caps_accel_and_restores():
    printer = _known_state_printer()
    toolhead = printer.lookup_object("toolhead")
    toolhead.max_accel = 5000.0

    with KnownKinematicLimits(printer):  # pyright: ignore[reportArgumentType]
        assert toolhead.max_accel == MAX_ACCEL

    assert toolhead.max_accel == 5000.0


def test_known_kinematic_limits_leaves_accel_unchanged_when_below_cap():
    printer = _known_state_printer()
    toolhead = printer.lookup_object("toolhead")
    toolhead.max_accel = 1500.0

    with KnownKinematicLimits(printer):  # pyright: ignore[reportArgumentType]
        assert toolhead.max_accel == 1500.0

    assert toolhead.max_accel == 1500.0


class _LegacyFakeToolhead:
    """Pre-Aug-2025 Klipper toolhead (no set_max_velocities)."""

    max_velocity = 200.0
    max_accel = 3000.0
    square_corner_velocity = 5.0
    min_cruise_ratio = 0.5
    calc_calls = 0

    def _calc_junction_deviation(self) -> None:
        _LegacyFakeToolhead.calc_calls += 1


def test_known_kinematic_limits_legacy_toolhead_caps_and_restores():
    _LegacyFakeToolhead.calc_calls = 0
    printer = FakePrinter(toolhead=_LegacyFakeToolhead())
    toolhead = printer.lookup_object("toolhead")
    toolhead.square_corner_velocity = 15.0
    toolhead.max_accel = 5000.0

    with KnownKinematicLimits(printer):  # pyright: ignore[reportArgumentType]
        assert toolhead.square_corner_velocity == MAX_SCV
        assert toolhead.max_accel == MAX_ACCEL
        assert _LegacyFakeToolhead.calc_calls == 1

    assert toolhead.square_corner_velocity == 15.0
    assert toolhead.max_accel == 5000.0
    assert _LegacyFakeToolhead.calc_calls == 2


def test_known_kinematic_limits_disables_and_enables_input_shaper():
    _FakeInputShaper.disable_calls = 0
    _FakeInputShaper.enable_calls = 0
    printer = _known_state_printer()

    with KnownKinematicLimits(printer):  # pyright: ignore[reportArgumentType]
        assert _FakeInputShaper.disable_calls == 1
        assert _FakeInputShaper.enable_calls == 0

    assert _FakeInputShaper.disable_calls == 1
    assert _FakeInputShaper.enable_calls == 1
