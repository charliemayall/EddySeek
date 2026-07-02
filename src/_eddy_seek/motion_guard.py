"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

Clear and restore XY gcode offset for the duration of a seek session.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .common import Position
from logging import getLogger

logger = getLogger(__name__)

if TYPE_CHECKING:
    from klippy.gcode import GCodeCommand
    from klippy.klippy import Printer


def _get_gcode_offset_xy(printer: Printer) -> Position:
    gcode_move = printer.lookup_object("gcode_move")
    hp = gcode_move.homing_position
    return Position.from_pair(hp)


def _set_gcode_offset_xy(printer: Printer, pos: Position) -> None:
    gcode = printer.lookup_object("gcode")
    gcode.run_script_from_command(f"SET_GCODE_OFFSET {pos.to_gcode()}")


def clear_gcode_offset_xy(printer: Printer) -> None:
    """Zero XY gcode offset so alignment moves use machine coordinates."""
    _set_gcode_offset_xy(printer, Position.zero())


def _apply_toolhead_limits(
    toolhead: Any,
    *,
    square_corner_velocity: float | None,
    min_cruise_ratio: float | None,
) -> None:
    """Set square_corner_velocity / min_cruise_ratio and recalc junction deviation."""
    if square_corner_velocity is not None:
        toolhead.square_corner_velocity = square_corner_velocity
    if min_cruise_ratio is not None and hasattr(toolhead, "min_cruise_ratio"):
        toolhead.min_cruise_ratio = min_cruise_ratio
    calc = getattr(toolhead, "_calc_junction_deviation", None)
    if calc is not None:
        calc()


class MotionGuard:
    """Restore gcode-offset settings after a seek session."""

    def __init__(self, printer: Printer, gcmd: GCodeCommand | None = None) -> None:
        self._printer = printer
        self._gcmd = gcmd
        self._input_shaper: Any | None = None
        self._saved_scv: float | None = None
        self._saved_mcr: float | None = None
        self._saved_gcode_offset: Position | None = None

    def __enter__(self) -> MotionGuard:
        self._saved_gcode_offset = _get_gcode_offset_xy(self._printer)
        _set_gcode_offset_xy(self._printer, Position.zero())

        toolhead = self._printer.lookup_object("toolhead")
        self._saved_scv = toolhead.square_corner_velocity
        self._saved_mcr = (
            toolhead.min_cruise_ratio if hasattr(toolhead, "min_cruise_ratio") else None
        )
        applied_scv = min(9.0, self._saved_scv)
        _apply_toolhead_limits(
            toolhead,
            square_corner_velocity=applied_scv,
            min_cruise_ratio=0.0 if self._saved_mcr is not None else None,
        )
        logger.debug(f"EDDY_SEEK: set square_corner_velocity to {applied_scv}")

        self._input_shaper = self._printer.lookup_object("input_shaper", None)
        if self._input_shaper is not None:
            self._input_shaper.disable_shaping()
            logger.debug("EDDY_SEEK: disabled input shaping")
        else:
            logger.debug("EDDY_SEEK: no input shaping found")

        self._respond("EDDY_SEEK: cleared gcode offset")
        return self

    def __exit__(self, *exc: object) -> None:
        if self._saved_gcode_offset is not None:
            _set_gcode_offset_xy(self._printer, self._saved_gcode_offset)
            logger.debug(
                f"EDDY_SEEK: restored gcode offset: {self._saved_gcode_offset}"
            )
        toolhead = self._printer.lookup_object("toolhead")
        if self._saved_scv is not None:
            _apply_toolhead_limits(
                toolhead,
                square_corner_velocity=self._saved_scv,
                min_cruise_ratio=self._saved_mcr,
            )
            logger.debug(
                f"EDDY_SEEK: restored toolhead limits: square_corner_velocity={self._saved_scv}, min_cruise_ratio={self._saved_mcr}"
            )
        else:
            logger.debug("EDDY_SEEK: no toolhead limits found")

        if self._input_shaper is not None:
            self._input_shaper.enable_shaping()
            logger.debug("EDDY_SEEK: enabled input shaping")
        else:
            logger.debug("EDDY_SEEK: no input shaping found")

        self._respond("EDDY_SEEK: restored motion settings")
        return None

    def _respond(self, msg: str) -> None:
        if self._gcmd is not None:
            self._gcmd.respond_info(msg)
