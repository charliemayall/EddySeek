"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

SeekConfig and printer.cfg section parsing.

Field ``metadata`` drives validation, ``EDDY_SEEK_SET`` parsing, and (mostly)
``load_seek_config``:

- ``gcode`` - G-code param name; presence means runtime-settable
- ``positive`` - float must be > 0
- ``speed`` - cfg/G-code value is mm/s; stored internally as mm/min
- ``min`` - int must be >= this value
- ``enum`` - allowed string values
- ``bool`` - parse true/false/1/0 from G-code strings
"""

from __future__ import annotations

import logging
from dataclasses import Field, asdict, dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

if TYPE_CHECKING:
    from klippy.extras.configfile import ConfigWrapper
    from klippy.gcode import GCodeCommand

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class SeekConfig:
    max_jog_x: float = field(
        default=2.5, metadata={"gcode": "MAX_JOG_X", "positive": True}
    )
    max_jog_y: float = field(
        default=2.5, metadata={"gcode": "MAX_JOG_Y", "positive": True}
    )
    tolerance: float = field(
        default=0.05, metadata={"gcode": "TOLERANCE", "positive": True}
    )
    dwell_time: float = field(
        default=0.5, metadata={"gcode": "DWELL_TIME", "positive": True}
    )
    jog_speed: float = field(
        default=80 * 60.0,
        metadata={"gcode": "JOG_SPEED", "positive": True, "speed": True},
    )
    search_for: Literal["min", "max"] = field(
        default="max",
        metadata={"gcode": "SEARCH_FOR", "enum": ("min", "max")},
    )
    strategy: Literal["centroid", "sweep_centroid", "debug_scan", "circle_harmonic"] = (
        field(
            default="sweep_centroid",
            metadata={
                "gcode": "STRATEGY",
                "enum": (
                    "centroid",
                    "sweep_centroid",
                    "debug_scan",
                    "circle_harmonic",
                ),
            },
        )
    )
    max_passes: int = field(default=6, metadata={"gcode": "MAX_PASSES", "min": 1})
    save_session_trace: bool = field(
        default=False, metadata={"gcode": "SAVE_SESSION_TRACE", "bool": True}
    )
    save_plots: bool = field(
        default=False, metadata={"gcode": "SAVE_PLOTS", "bool": True}
    )
    result_folder: str = field(default="~/printer_data/config/eddy_seek_results")

    sweep_coarse_speed: float = field(
        default=20 * 60.0,
        metadata={"gcode": "SWEEP_COARSE_SPEED", "positive": True, "speed": True},
    )
    sweep_fine_speed: float = field(
        default=10 * 60.0,
        metadata={"gcode": "SWEEP_FINE_SPEED", "positive": True, "speed": True},
    )
    sweep_overscan: float = field(
        default=1.0, metadata={"gcode": "SWEEP_OVERSCAN", "positive": True}
    )
    sweep_cross_offset: float = field(
        default=0.3, metadata={"gcode": "SWEEP_CROSS_OFFSET", "positive": True}
    )
    fine_shrink: float = field(
        default=0.6, metadata={"gcode": "FINE_SHRINK", "positive": True}
    )
    min_sweep_samples: int = field(
        default=20, metadata={"gcode": "MIN_SWEEP_SAMPLES", "min": 3}
    )
    circle_radius_start: float = field(
        default=2.0, metadata={"gcode": "CIRCLE_RADIUS_START", "positive": True}
    )
    circle_radius_min: float = field(
        default=0.5, metadata={"gcode": "CIRCLE_RADIUS_MIN", "positive": True}
    )
    circle_shrink: float = field(
        default=0.6, metadata={"gcode": "CIRCLE_SHRINK", "positive": True}
    )
    circle_arc_resolution: float = field(
        default=0.1, metadata={"gcode": "CIRCLE_ARC_RESOLUTION", "positive": True}
    )
    circle_speed: float = field(
        default=10 * 60.0,
        metadata={"gcode": "CIRCLE_SPEED", "positive": True, "speed": True},
    )
    circle_lead_in: float = field(
        default=0.25,
        metadata={"gcode": "CIRCLE_LEAD_IN"},
    )
    noise_k: float = field(default=1.0, metadata={"gcode": "NOISE_K", "positive": True})
    harmonic_step_gain: float = field(
        default=0.15, metadata={"gcode": "HARMONIC_STEP_GAIN", "positive": True}
    )
    harmonic_min_quality: float = field(
        default=0.5, metadata={"gcode": "HARMONIC_MIN_QUALITY", "positive": True}
    )
    circle_refresh_sweeps: bool = field(
        default=False,
        metadata={"gcode": "CIRCLE_REFRESH_SWEEPS", "bool": True},
    )
    circle_skip_bootstrap: bool = field(
        default=False,
        metadata={"gcode": "CIRCLE_SKIP_BOOTSTRAP", "bool": True},
    )
    debug: bool = field(default=False, metadata={"bool": True})

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


def _runtime_settable_map() -> dict[str, str]:
    """Get the map of G-code keys to SeekConfig field names, for fields with a "gcode" metadata key (runtime settable)"""
    return {
        spec.metadata["gcode"]: spec.name
        for spec in fields(SeekConfig)
        if "gcode" in spec.metadata
    }


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


def _parse_runtime_value(field_name: str, label: str, raw: Any) -> Any:
    meta = _seek_field(field_name).metadata
    if meta.get("bool"):
        return _parse_bool(raw, label)
    if "enum" in meta:
        text = str(raw).lower()
        if text not in meta["enum"]:
            raise ValueError(
                f"invalid {label}={raw!r}, allowed: {', '.join(meta['enum'])}"
            )
        return text
    if "min" in meta:
        parsed = int(raw)
        if parsed < meta["min"]:
            raise ValueError(f"{label} must be >= {meta['min']}")
        return parsed
    if meta.get("positive"):
        parsed = float(raw)
        if parsed <= 0.0:
            raise ValueError(f"{label} must be > 0")
        if meta.get("speed"):
            return _mm_s_to_mm_min(parsed)
        return parsed
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
        value = getattr(cfg, spec.name)
        meta = spec.metadata
        if meta.get("positive") and value <= 0.0:
            raise ValueError(f"{spec.name} must be > 0")
        if "min" in meta and value < meta["min"]:
            raise ValueError(f"{spec.name} must be >= {meta['min']}")
        if "enum" in meta and value not in meta["enum"]:
            raise ValueError(
                f"{spec.name} must be one of {meta['enum']!r} (got {value!r})"
            )

    if cfg.circle_radius_min > cfg.circle_radius_start:
        raise ValueError(
            "circle_radius_min must be <= circle_radius_start "
            f"(got {cfg.circle_radius_min} > {cfg.circle_radius_start})"
        )
    if not 0.0 <= cfg.circle_lead_in < 1.0:
        raise ValueError(f"circle_lead_in must be in [0, 1) (got {cfg.circle_lead_in})")


def _config_option_set(config: Any, key: str) -> bool:
    options = getattr(config, "_options", None)
    if options is not None:
        return key in options
    fileconfig = getattr(config, "fileconfig", None)
    section = getattr(config, "section", None)
    if fileconfig is not None and section is not None:
        return fileconfig.has_option(section, key)
    return False


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
        if config.getboolean("save_sweep_plots", False) and not _config_option_set(
            config, "save_plots"
        ):
            values["save_plots"] = True  # legacy key; save_plots wins if both set
        cfg = SeekConfig(**values)
        logger.info(f"eddy_seek: loaded config {cfg.format_seek_config()}")
        return cfg
    except ValueError as exc:
        raise config.error(f"eddy_seek: {exc}") from exc
