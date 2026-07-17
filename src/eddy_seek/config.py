"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

SeekConfig - parsing of [eddy_seek] section in printer.cfg.

Field ``metadata`` drives validation, ``EDDY_SEEK_SET`` parsing, ``load_seek_config``,
and generated USER_GUIDE reference tables:

- ``gcode`` - G-code param name; presence means runtime-settable
- ``positive`` - float must be > 0
- ``speed`` - cfg/G-code value is mm/s; stored internally as mm/min
- ``min`` - int must be >= this value
- ``enum`` - allowed string values
- ``bool`` - parse true/false/1/0 from G-code strings
- ``doc`` - description for generated docs
"""

from __future__ import annotations

import logging
from dataclasses import Field, asdict, dataclass, field, fields
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

if TYPE_CHECKING:
    from klippy.configfile import ConfigWrapper
    from klippy.gcode import GCodeCommand

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class SeekConfig:
    max_jog_x: float = field(
        default=2.5,
        metadata={
            "gcode": "MAX_JOG_X",
            "positive": True,
            "doc": "Max search radius from start (mm)",
        },
    )
    max_jog_y: float = field(
        default=2.5,
        metadata={
            "gcode": "MAX_JOG_Y",
            "positive": True,
            "doc": "Max search radius from start (mm)",
        },
    )
    tolerance: float = field(
        default=0.05,
        metadata={
            "gcode": "TOLERANCE",
            "positive": True,
            "doc": "Stop when both axes move less than this (mm)",
        },
    )
    dwell_time: float = field(
        default=0.5,
        metadata={
            "gcode": "DWELL_TIME",
            "positive": True,
            "doc": "Seconds at each probe point (grid strategies only)",
        },
    )
    jog_speed: float = field(
        default=80 * 60.0,
        metadata={
            "gcode": "JOG_SPEED",
            "positive": True,
            "speed": True,
            "doc": "Feedrate for search jogs (mm/s)",
        },
    )
    search_for: Literal["min", "max"] = field(
        default="max",
        metadata={
            "gcode": "SEARCH_FOR",
            "enum": ("min", "max"),
            "doc": "Which frequency extreme marks the nozzle centre (`max` for most users)",
        },
    )
    strategy: Literal["centroid", "sweep_centroid", "debug_scan"] = field(
        default="sweep_centroid",
        metadata={
            "gcode": "STRATEGY",
            "enum": (
                "centroid",
                "sweep_centroid",
                "debug_scan",
            ),
            "doc": "`sweep_centroid`, `centroid`, or `debug_scan` (diag only)",
        },
    )
    max_passes: int = field(
        default=6,
        metadata={
            "gcode": "MAX_PASSES",
            "min": 1,
            "doc": "Search passes before giving up",
        },
    )
    save_session_trace: bool = field(
        default=False,
        metadata={
            "gcode": "SAVE_SESSION_TRACE",
            "bool": True,
            "doc": "Write probe JSON to `result_folder` (debug)",
        },
    )
    save_plots: bool = field(
        default=False,
        metadata={
            "gcode": "SAVE_PLOTS",
            "bool": True,
            "doc": "Write HTML plots to `result_folder` (needs plotly)",
        },
    )
    result_folder: str = field(
        default="~/printer_data/config/eddy_seek_results",
        metadata={"doc": "Output directory for debug artefacts"},
    )

    sweep_coarse_speed: float = field(
        default=20 * 60.0,
        metadata={
            "gcode": "SWEEP_COARSE_SPEED",
            "positive": True,
            "speed": True,
            "doc": "Coarse sweep feedrate (mm/s)",
        },
    )
    sweep_fine_speed: float = field(
        default=10 * 60.0,
        metadata={
            "gcode": "SWEEP_FINE_SPEED",
            "positive": True,
            "speed": True,
            "doc": "Fine sweep feedrate (mm/s)",
        },
    )
    sweep_overscan: float = field(
        default=1.0,
        metadata={
            "gcode": "SWEEP_OVERSCAN",
            "positive": True,
            "doc": "Extra travel beyond jog range (mm)",
        },
    )
    sweep_cross_offset: float = field(
        default=0.3,
        metadata={
            "gcode": "SWEEP_CROSS_OFFSET",
            "positive": True,
            "doc": "Stagger between parallel sweeps (mm)",
        },
    )
    fine_shrink: float = field(
        default=0.6,
        metadata={
            "gcode": "FINE_SHRINK",
            "positive": True,
            "doc": "Fine pass range multiplier (x max_jog)",
        },
    )
    min_sweep_samples: int = field(
        default=20,
        metadata={
            "gcode": "MIN_SWEEP_SAMPLES",
            "min": 3,
            "doc": "Minimum profile points before centroid fit",
        },
    )
    coarse_phases: int = field(
        default=2,
        metadata={
            "gcode": "COARSE_PHASES",
            "min": 1,
            "doc": "Coarse search passes before fine passes",
        },
    )
    coarse_cross_passes: int = field(
        default=3,
        metadata={
            "gcode": "COARSE_CROSS_PASSES",
            "min": 1,
            "doc": "Staggered sweep lines per coarse pass (fine uses 1)",
        },
    )
    sweep_arc_resolution: float = field(
        default=0.1,
        metadata={
            "gcode": "SWEEP_ARC_RESOLUTION",
            "positive": True,
            "doc": "Max chord length per connector arc between sweeps (mm)",
        },
    )
    debug: bool = field(
        default=False,
        metadata={
            "bool": True,
            "doc": "Verbose console; pass `VERBOSE=1` on any command for one-off verbosity",
        },
    )

    def __post_init__(self) -> None:
        _validate(self)
        self.result_folder = str(Path(self.result_folder).expanduser().resolve())

    @property
    def grid_step_x(self) -> float:
        return self.max_jog_x / 2.0

    @property
    def grid_step_y(self) -> float:
        return self.max_jog_y / 2.0

    def format_seek_config(self) -> str:
        """One-line summary of effective alignment settings (speeds in mm/s)."""
        parts: list[str] = []
        for key, value in self.to_dict().items():
            if _is_speed_field(key):
                value = value / 60.0
            parts.append(f"{key}={value}")
        return ", ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def strategy_from_gcmd(self, gcmd: GCodeCommand) -> str:
        """Resolve optional per-command ``STRATEGY=`` using SeekConfig enum rules."""
        return _strategy_from_gcmd(gcmd, self.strategy)

    def apply_runtime_set(self, gcmd: GCodeCommand) -> list[str]:
        """
        Apply ``EDDY_SEEK_SET`` parameters in place.

        Only keys present on the G-code command line are changed.

        Returns:
          list[str]: A list of ``field=value`` strings describing what changed.
        """
        params = gcmd.get_command_parameters()
        changes: list[str] = []
        if not params:
            return changes
        gcode_map = _runtime_settable_map()
        for gcode_key, gcode_raw in params.items():
            gcode_key = gcode_key.upper()
            check = _can_set_key(gcode_key)
            if not check.is_field or not check.is_settable:
                raise gcmd.error(f"EDDY_SEEK_SET: {check.error}")
            config_field_name = gcode_map[gcode_key]
            try:
                value = _parse_runtime_value(config_field_name, gcode_key, gcode_raw)
            except ValueError as exc:
                raise gcmd.error(
                    f"EDDY_SEEK_SET: invalid {gcode_key}={gcode_raw!r} ({exc})"
                ) from exc
            setattr(self, config_field_name, value)
            display = value / 60.0 if _is_speed_field(config_field_name) else value
            changes.append(f"{config_field_name} --> {display}")
        try:
            _validate(self)
        except ValueError as exc:
            raise gcmd.error(f"EDDY_SEEK_SET: {exc}") from exc
        if changes:
            logger.info(f"eddy_seek: runtime config updated: {', '.join(changes)}")
        return changes


def _seek_field(name: str) -> Field[Any]:
    for spec in fields(SeekConfig):
        if spec.name == name:
            return spec
    raise KeyError(name)


def _is_speed_field(name: str) -> bool:
    return _seek_field(name).metadata.get("speed") is True


def _mm_s_to_mm_min(mm_s: float) -> float:
    return mm_s * 60.0


@lru_cache(maxsize=1)
def _runtime_settable_map() -> dict[str, str]:
    """Get the map of G-code keys to SeekConfig field names, for fields with a "gcode" metadata key (runtime settable)"""
    return {
        spec.metadata["gcode"]: spec.name
        for spec in fields(SeekConfig)
        if "gcode" in spec.metadata
    }


def _validate_field_value(
    field_name: str, value: Any, *, label: str | None = None
) -> None:
    meta = _seek_field(field_name).metadata
    name = label or field_name
    if meta.get("positive") and value < 0.0:
        raise ValueError(f"{name} must be >= 0")
    if "min" in meta and value < meta["min"]:
        raise ValueError(f"{name} must be >= {meta['min']}")
    if "enum" in meta and value not in meta["enum"]:
        raise ValueError(f"{name} must be one of {meta['enum']!r} (got {value!r})")


class SetKeyCheck(NamedTuple):
    is_field: bool
    is_settable: bool
    error: str | None = None


def _can_set_key(key: str) -> SetKeyCheck:
    """
    Check whether a G-code key names a SeekConfig field and may be set at runtime.

    Some fields exist only in printer.cfg (no ``gcode`` metadata); others are unknown.
    """
    gcode_key = key.upper()
    gcode_map = _runtime_settable_map()
    is_field = gcode_key.lower() in {spec.name for spec in fields(SeekConfig)}
    is_settable = gcode_key in gcode_map
    if not is_field:
        return SetKeyCheck(
            is_field=False,
            is_settable=False,
            error=(
                f"Unknown parameter {key!r}</br>Known: {', '.join(sorted(gcode_map))}"
            ),
        )
    if not is_settable:
        return SetKeyCheck(
            is_field=True,
            is_settable=False,
            error=f"{key!r} Can only be set via your config file",
        )
    return SetKeyCheck(is_field=True, is_settable=True)


def _strategy_from_gcmd(gcmd: GCodeCommand, default: str) -> str:
    params = gcmd.get_command_parameters()
    if "STRATEGY" not in params:
        return default
    raw = params["STRATEGY"]
    try:
        return _parse_runtime_value("strategy", "STRATEGY", raw)
    except ValueError as exc:
        raise gcmd.error(f"invalid STRATEGY={raw!r} ({exc})") from exc


def _parse_runtime_value(field_name: str, label: str, raw: Any) -> Any:
    meta = _seek_field(field_name).metadata
    if meta.get("bool"):
        return _parse_bool(raw, label)
    if "enum" in meta:
        value = str(raw).lower()
        _validate_field_value(field_name, value, label=label)
        return value
    if "min" in meta:
        value = int(raw)
        _validate_field_value(field_name, value, label=label)
        return value
    if meta.get("positive"):
        value = float(raw)
        if meta.get("speed"):
            value = _mm_s_to_mm_min(value)
        _validate_field_value(field_name, value, label=label)
        return value
    raise ValueError(f"EDDY_SEEK_SET: invalid {label}={raw!r}")


def _field_name_for_key(gcode_key: str) -> str:
    gcode_key = gcode_key.upper()
    gcode_map = _runtime_settable_map()
    if gcode_key in gcode_map:
        return gcode_map[gcode_key]
    if gcode_key.lower() in {spec.name for spec in fields(SeekConfig)}:
        return gcode_key.lower()
    raise ValueError(f"unknown parameter {gcode_key!r}")


def _parse_bool(raw: Any, label: str) -> bool:
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"invalid {label}={raw!r}")


def _validate(cfg: SeekConfig) -> None:
    for spec in fields(SeekConfig):
        _validate_field_value(spec.name, getattr(cfg, spec.name))


def load_seek_config(config: ConfigWrapper) -> SeekConfig:
    """Parse alignment options from an ``[eddy_seek]`` config section."""
    d = SeekConfig()
    values: dict[str, Any] = {}
    try:
        for spec in fields(SeekConfig):
            name = spec.name
            default = getattr(d, name)
            if spec.metadata.get("bool"):
                values[name] = config.getboolean(name, default)
            elif "enum" in spec.metadata:
                values[name] = config.get(name, default).lower()
            elif isinstance(default, int):
                values[name] = config.getint(name, default)
            elif spec.metadata.get("speed"):
                default_mm_s = default / 60.0
                values[name] = _mm_s_to_mm_min(config.getfloat(name, default_mm_s))
            elif isinstance(default, float):
                values[name] = config.getfloat(name, default)
            else:
                values[name] = config.get(name, default)
        cfg = SeekConfig(**values)
        logger.info(f"eddy_seek: loaded config {cfg.format_seek_config()}")
        return cfg
    except ValueError as exc:
        raise config.error(f"eddy_seek: {exc}") from exc
