"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

SeekConfig and printer.cfg section parsing.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
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
    "grid_step_x": 2.5,
    "grid_step_y": 2.5,
    "max_iter": 10,
    "max_passes": 6,
}

# G-code param name → (field name, kind)
_RUNTIME_SETTABLE: dict[str, tuple[str, str]] = {
    "WINDOW_SIZE": ("window_size", "int"),
    "MAX_JOG_X": ("max_jog_x", "float"),
    "MAX_JOG_Y": ("max_jog_y", "float"),
    "TOLERANCE": ("tolerance", "float"),
    "DWELL_TIME": ("dwell_time", "float"),
    "JOG_SPEED": ("jog_speed", "float"),
    "SEARCH_FOR": ("search_for", "enum"),
    "STRATEGY": ("strategy", "enum"),
    "GRID_STEP_X": ("grid_step_x", "float"),
    "GRID_STEP_Y": ("grid_step_y", "float"),
    "MAX_ITER": ("max_iter", "int"),
    "MAX_PASSES": ("max_passes", "int"),
}

_ENUMS: dict[str, tuple[str, ...]] = {
    "search_for": ("min", "max"),
    "strategy": ("ternary", "centroid"),
}

_POSITIVE_FLOATS = (
    "max_jog_x",
    "max_jog_y",
    "tolerance",
    "dwell_time",
    "jog_speed",
    "grid_step_x",
    "grid_step_y",
)


@dataclass(kw_only=True)
class SeekConfig:
    window_size: int = _DEFAULTS["window_size"]
    max_jog_x: float = _DEFAULTS["max_jog_x"]
    max_jog_y: float = _DEFAULTS["max_jog_y"]
    tolerance: float = _DEFAULTS["tolerance"]
    dwell_time: float = _DEFAULTS["dwell_time"]
    jog_speed: float = _DEFAULTS["jog_speed"]
    search_for: Literal["min", "max"] = _DEFAULTS["search_for"]
    strategy: Literal["ternary", "centroid"] = _DEFAULTS["strategy"]
    grid_step_x: float = _DEFAULTS["grid_step_x"]
    grid_step_y: float = _DEFAULTS["grid_step_y"]
    max_iter: int = _DEFAULTS["max_iter"]
    max_passes: int = _DEFAULTS["max_passes"]

    def __post_init__(self) -> None:
        if self.window_size < 1:
            raise ValueError("window_size must be >= 1")
        for name in _POSITIVE_FLOATS:
            if getattr(self, name) <= 0.0:
                raise ValueError(f"{name} must be > 0")
        if self.max_iter < 1:
            raise ValueError("max_iter must be >= 1")
        if self.max_passes < 1:
            raise ValueError("max_passes must be >= 1")
        if self.search_for not in _ENUMS["search_for"]:
            raise ValueError(
                f"search_for must be 'min' or 'max' (got {self.search_for!r})"
            )
        if self.strategy not in _ENUMS["strategy"]:
            raise ValueError(
                f"strategy must be 'ternary' or 'centroid' (got {self.strategy!r})"
            )

    def format_seek_config(self) -> str:
        """One-line summary of effective alignment settings."""
        return ", ".join(f"{key}={value}" for key, value in self.to_dict().items())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def _clean_var(self, key: str, value: Any) -> Any:
        spec = _RUNTIME_SETTABLE.get(key.upper())
        if spec is None:
            raise ValueError(f"EDDY_SEEK_SET: unknown parameter {key!r}")
        field_name, kind = spec
        if kind == "int":
            parsed = int(value)
            if parsed < 1:
                raise ValueError(f"{key} must be >= 1")
            return parsed
        if kind == "float":
            parsed = float(value)
            if parsed <= 0.0:
                raise ValueError(f"{key} must be > 0")
            return parsed
        if kind == "enum":
            text = str(value).lower()
            if text in _ENUMS[field_name]:
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
                raise gcmd.error(
                    f"EDDY_SEEK_SET: unknown parameter {key!r} "
                    f"(known: {', '.join(sorted(_RUNTIME_SETTABLE))})"
                )
            field_name, _kind = spec
            try:
                value = self._clean_var(key, raw)
                setattr(self, field_name, value)
                changes.append(f"{field_name}={value}")
            except ValueError as exc:
                raise gcmd.error(
                    f"EDDY_SEEK_SET: invalid {key}={raw!r} ({exc})"
                ) from exc
        return changes


def load_seek_config(config) -> SeekConfig:
    """Parse alignment options from an ``[eddy_seek]`` config section."""
    d = _DEFAULTS
    max_jog_x = config.getfloat("max_jog_x", d["max_jog_x"])
    max_jog_y = config.getfloat("max_jog_y", d["max_jog_y"])
    try:
        return SeekConfig(
            window_size=config.getint("window_size", d["window_size"]),
            max_jog_x=max_jog_x,
            max_jog_y=max_jog_y,
            tolerance=config.getfloat("tolerance", d["tolerance"]),
            dwell_time=config.getfloat("dwell_time", d["dwell_time"]),
            jog_speed=config.getfloat("jog_speed", d["jog_speed"]),
            search_for=config.get("search_for", d["search_for"]).lower(),
            strategy=config.get("strategy", d["strategy"]).lower(),
            grid_step_x=config.getfloat("grid_step_x", max_jog_x / 2.0),
            grid_step_y=config.getfloat("grid_step_y", max_jog_y / 2.0),
            max_iter=config.getint("max_iter", d["max_iter"]),
            max_passes=config.getint("max_passes", d["max_passes"]),
        )
    except ValueError as exc:
        raise config.error(f"eddy_seek: {exc}")
