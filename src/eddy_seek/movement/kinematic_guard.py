"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Toolhead kinematic limits and input-shaper guard for seek sessions.
"""

from __future__ import annotations

from logging import getLogger
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from klippy.klippy import Printer
    from klippy.toolhead import ToolHead

logger = getLogger(__name__)

MAX_SCV = 10.0
MAX_ACCEL = 3000.0
MCR_DEFAULT = 0.5


def set_kinematic_limits(
    toolhead: ToolHead,
    *,
    max_velocity: float | None = None,
    max_accel: float | None = None,
    square_corner_velocity: float | None = None,
    min_cruise_ratio: float | None = None,
) -> None:
    """Set toolhead velocity limits (Klipper >= Aug 2025 or legacy fallback)."""
    if hasattr(toolhead, "set_max_velocities"):
        toolhead.set_max_velocities(
            max_velocity, max_accel, square_corner_velocity, min_cruise_ratio
        )
        return
    if max_velocity is not None:
        toolhead.max_velocity = max_velocity
    if max_accel is not None:
        toolhead.max_accel = max_accel
    if square_corner_velocity is not None:
        toolhead.square_corner_velocity = square_corner_velocity
    if min_cruise_ratio is not None and hasattr(toolhead, "min_cruise_ratio"):
        toolhead.min_cruise_ratio = min_cruise_ratio
    if hasattr(toolhead, "_calc_junction_deviation"):
        toolhead._calc_junction_deviation()


def _read_max_accel(toolhead: ToolHead) -> float:
    if hasattr(toolhead, "get_max_velocity"):
        _, max_accel = toolhead.get_max_velocity()
        return max_accel
    return toolhead.max_accel


class KnownKinematicLimits:
    """Save and restore toolhead SCV/cruise limits and input shaper for seeks.

    Gcode XY offset is not handled here - ``SAVE_GCODE_STATE`` / ``RESTORE`` and
    ``clear_gcode_offset_xy()`` own that at the session boundary.
    """

    def __init__(self, printer: Printer) -> None:
        self._printer = printer
        self._saved_scv: float | None = None
        self._saved_mcr: float | None = None
        self._saved_accel: float | None = None

    def __enter__(self) -> KnownKinematicLimits:
        toolhead = self._printer.lookup_object("toolhead")
        self._saved_scv = toolhead.square_corner_velocity
        self._saved_mcr = getattr(toolhead, "min_cruise_ratio", None)
        self._saved_accel = _read_max_accel(toolhead)
        applied_scv = min(MAX_SCV, self._saved_scv)
        applied_accel = min(MAX_ACCEL, self._saved_accel)

        logger.info(
            f"EDDY_SEEK: Requested kinematic: square_corner_velocity={applied_scv}, accel={applied_accel}"
        )
        logger.info(
            f"EDDY_SEEK: scv: {self._saved_scv}, mcr: {self._saved_mcr} accel: {self._saved_accel}"
        )
        set_kinematic_limits(
            toolhead,
            max_velocity=None,
            max_accel=applied_accel,
            square_corner_velocity=applied_scv,
            min_cruise_ratio=(
                MCR_DEFAULT if self._saved_mcr is None else self._saved_mcr
            ),
        )
        logger.info(
            f"EDDY_SEEK: set scv: {applied_scv}, mcr: {MCR_DEFAULT if self._saved_mcr is None else self._saved_mcr}, accel: {applied_accel}"
        )

        input_shaper = self._printer.lookup_object("input_shaper", None)
        if input_shaper is not None:
            input_shaper.disable_shaping()
            logger.info("EDDY_SEEK: disabled input shaping")
        else:
            logger.info("EDDY_SEEK: no input shaping found")

        return self

    def __exit__(self, *exc: object) -> None:
        toolhead = self._printer.lookup_object("toolhead")
        if self._saved_scv is not None:
            set_kinematic_limits(
                toolhead,
                max_velocity=None,
                max_accel=self._saved_accel,
                square_corner_velocity=self._saved_scv,
                min_cruise_ratio=self._saved_mcr,
            )
            logger.info(
                f"EDDY_SEEK: restored toolhead limits: scv: {self._saved_scv}, mcr: {self._saved_mcr}, accel: {self._saved_accel}"
            )

        input_shaper = self._printer.lookup_object("input_shaper", None)
        if input_shaper is not None:
            input_shaper.enable_shaping()
            logger.info("EDDY_SEEK: enabled input shaping")

        return
