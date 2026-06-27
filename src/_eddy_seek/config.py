"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

SeekConfig and printer.cfg section parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
import logging

logger = logging.getLogger(__name__)

_DEFAULTS = {
    "window_size": 20,
    "max_jog_x": 5.0,
    "max_jog_y": 5.0,
    "tolerance": 0.1,
    "dwell_time": 0.5,
    "jog_speed": 600.0,
    "search_for": "max",
    "strategy": "ternary",
    "max_iter": 10,
    "max_passes": 6,
}

# G-code param name → (field, (kind / validator))
_RUNTIME_SETTABLE = {
    "WINDOW_SIZE": ("window_size", "int"),
    "MAX_JOG_X": ("max_jog_x", "float"),
    "MAX_JOG_Y": ("max_jog_y", "float"),
    "TOLERANCE": ("tolerance", "float"),
    "DWELL_TIME": ("dwell_time", "float"),
    "JOG_SPEED": ("jog_speed", "float"),
    "SEARCH_FOR": ("search_for", lambda value: value.lower() in ("min", "max")),
    "STRATEGY": ("strategy", lambda value: value.lower() in ("ternary", "centroid")),
    "GRID_STEP_X": ("grid_step_x", "float"),
    "GRID_STEP_Y": ("grid_step_y", "float"),
    "MAX_ITER": ("max_iter", "int"),
    "MAX_PASSES": ("max_passes", "int"),
}


@dataclass
class SeekConfig:
    window_size: int
    max_jog_x: float
    max_jog_y: float
    tolerance: float
    dwell_time: float
    jog_speed: float
    search_for: Literal["min", "max"]
    strategy: Literal["ternary", "centroid"]
    grid_step_x: float
    grid_step_y: float
    max_iter: int
    max_passes: int

    @staticmethod
    def load_seek_config(config) -> SeekConfig:
        """Parse alignment options from a ``[eddy_seek]`` config section."""
        window_size = config.getint("window_size", _DEFAULTS["window_size"])
        max_jog_x = config.getfloat("max_jog_x", _DEFAULTS["max_jog_x"], above=0.0)
        max_jog_y = config.getfloat("max_jog_y", _DEFAULTS["max_jog_y"], above=0.0)
        search_for = config.get("search_for", _DEFAULTS["search_for"]).lower()
        strategy = config.get("strategy", _DEFAULTS["strategy"]).lower()

        if not _RUNTIME_SETTABLE["SEARCH_FOR"][1](search_for):
            raise ValueError("EDDY_SEEK_SET: search_for must be in ('min', 'max')")
        if not _RUNTIME_SETTABLE["STRATEGY"][1](strategy):
            raise ValueError(
                "EDDY_SEEK_SET: strategy must be in ('ternary', 'centroid')"
            )

        return SeekConfig(
            window_size=window_size,
            max_jog_x=max_jog_x,
            max_jog_y=max_jog_y,
            tolerance=config.getfloat("tolerance", _DEFAULTS["tolerance"], above=0.0),
            dwell_time=config.getfloat(
                "dwell_time", _DEFAULTS["dwell_time"], above=0.0
            ),
            jog_speed=config.getfloat("jog_speed", _DEFAULTS["jog_speed"], above=0.0),
            search_for=search_for,
            strategy=strategy,
            grid_step_x=config.getfloat("grid_step_x", max_jog_x / 2.0, above=0.0),
            grid_step_y=config.getfloat("grid_step_y", max_jog_y / 2.0, above=0.0),
            max_iter=config.getint("max_iter", _DEFAULTS["max_iter"]),
            max_passes=config.getint("max_passes", _DEFAULTS["max_passes"]),
        )

    def format_seek_config(self) -> str:
        """One-line summary of effective alignment settings."""
        return (
            f"strategy={self.strategy}  search_for={self.search_for}  "
            f"max_jog=({self.max_jog_x},{self.max_jog_y})  "
            f"tolerance={self.tolerance}  "
            f"dwell={self.dwell_time}  jog_speed={self.jog_speed}  "
            f"grid_step=({self.grid_step_x},{self.grid_step_y})  "
            f"max_iter={self.max_iter}  max_passes={self.max_passes}  "
            f"window_size={self.window_size}"
        )

    def _clean_var(self, key: str, value: Any):
        spec = _RUNTIME_SETTABLE.get(key.upper())
        if spec is None:
            raise ValueError(f"EDDY_SEEK_SET: unknown parameter {key!r}")
        _field, kind_or_callable = spec

        if kind_or_callable == "int":
            parsed = int(value)
            if parsed < 1:
                raise ValueError(f"{key} must be >= 1")
            return parsed
        if kind_or_callable == "float":
            parsed = float(value)
            if parsed <= 0.0:
                raise ValueError(f"{key} must be > 0")
            return parsed
        if callable(kind_or_callable):
            text = str(value).lower()
            if kind_or_callable(text):
                return text
            raise ValueError(f"invalid {key}={value!r}")
        raise ValueError(f"EDDY_SEEK_SET: invalid {key}={value!r}")

    def _var_ok(self, key: str, value: Any) -> bool:
        try:
            self._clean_var(key, value)
            return True
        except ValueError:
            return False

    def apply_runtime_set(self, gcmd) -> list[str]:
        """
        Apply ``EDDY_SEEK_SET`` parameters in place.

        Only keys present on the G-code command line are changed.  Returns a
        list of ``field=value`` strings describing what changed.  Raises via
        ``gcmd.error()`` on invalid input.
        """
        params = gcmd.get_command_parameters()
        changes: list[str] = []
        if not params:
            return changes

        for key, raw in params.items():
            spec = _RUNTIME_SETTABLE.get(key.upper())
            if spec is None:
                gcmd.error(
                    f"EDDY_SEEK_SET: unknown parameter {key!r} "
                    f"(known: {', '.join(sorted(_RUNTIME_SETTABLE))})"
                )
            try:
                value = self._clean_var(key, raw)
                setattr(
                    self, spec[0], value  # pyright: ignore[reportOptionalSubscript]
                )
                changes.append(
                    f"{spec[0]}={value}"  # pyright: ignore[reportOptionalSubscript]
                )
            except ValueError as exc:
                gcmd.error(f"EDDY_SEEK_SET: invalid {key}={raw!r} ({exc})")
                logger.debug(f"EDDY_SEEK_SET: {key}={raw!r} ({exc}) {gcmd}")
        return changes
