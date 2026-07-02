"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

SeekConfig and printer.cfg section parsing.

Field ``metadata`` drives validation, ``EDDY_SEEK_SET`` parsing, and (mostly)
``load_seek_config``:

- ``gcode`` — G-code param name; presence means runtime-settable
- ``positive`` — float must be > 0
- ``min`` — int must be >= this value
- ``enum`` — allowed string values
- ``bool`` — parse true/false/1/0 from G-code strings
"""

from __future__ import annotations

from dataclasses import Field, asdict, dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
import logging

if TYPE_CHECKING:
    from klippy.extras.configfile import ConfigWrapper
    from klippy.gcode import GCodeCommand

logger = logging.getLogger(__name__)


@dataclass(kw_only=True)
class SeekConfig:
    window_size: int = field(default=20, metadata={"gcode": "WINDOW_SIZE", "min": 1})
    max_jog_x: float = field(
        default=5.0, metadata={"gcode": "MAX_JOG_X", "positive": True}
    )
    max_jog_y: float = field(
        default=5.0, metadata={"gcode": "MAX_JOG_Y", "positive": True}
    )
    tolerance: float = field(
        default=0.1, metadata={"gcode": "TOLERANCE", "positive": True}
    )
    dwell_time: float = field(
        default=0.5, metadata={"gcode": "DWELL_TIME", "positive": True}
    )
    jog_speed: float = field(
        default=600.0, metadata={"gcode": "JOG_SPEED", "positive": True}
    )
    search_for: Literal["min", "max"] = field(
        default="max",
        metadata={"gcode": "SEARCH_FOR", "enum": ("min", "max")},
    )
    strategy: Literal["ternary", "centroid", "sweep_centroid", "debug_scan"] = field(
        default="sweep_centroid",
        metadata={
            "gcode": "STRATEGY",
            "enum": ("ternary", "centroid", "sweep_centroid", "debug_scan"),
        },
    )
    grid_step_x: float = field(
        default=2.5, metadata={"gcode": "GRID_STEP_X", "positive": True}
    )
    grid_step_y: float = field(
        default=2.5, metadata={"gcode": "GRID_STEP_Y", "positive": True}
    )
    max_iter: int = field(default=10, metadata={"gcode": "MAX_ITER", "min": 1})
    max_passes: int = field(default=6, metadata={"gcode": "MAX_PASSES", "min": 1})
    save_session_trace: bool = field(
        default=False, metadata={"gcode": "SAVE_SESSION_TRACE", "bool": True}
    )
    save_plots: bool = field(
        default=False, metadata={"gcode": "SAVE_PLOTS", "bool": True}
    )
    result_folder: str = field(default="~/printer_data/config/eddy_seek_results")

    sweep_coarse_speed: float = field(
        default=20.0, metadata={"gcode": "SWEEP_COARSE_SPEED", "positive": True}
    )
    sweep_fine_speed: float = field(
        default=10.0, metadata={"gcode": "SWEEP_FINE_SPEED", "positive": True}
    )
    sweep_overscan: float = field(
        default=1.0, metadata={"gcode": "SWEEP_OVERSCAN", "positive": True}
    )
    sweep_cross_offset: float = field(
        default=0.3, metadata={"gcode": "SWEEP_CROSS_OFFSET", "positive": True}
    )
    sweep_cross_passes: int = field(
        default=3, metadata={"gcode": "SWEEP_CROSS_PASSES", "min": 1}
    )
    fine_shrink: float = field(
        default=0.4, metadata={"gcode": "FINE_SHRINK", "positive": True}
    )
    min_sweep_samples: int = field(
        default=20, metadata={"gcode": "MIN_SWEEP_SAMPLES", "min": 3}
    )

    def __post_init__(self) -> None:
        self.result_folder = str(Path(self.result_folder).expanduser().resolve())
        _validate(self)

    def format_seek_config(self) -> str:
        """One-line summary of effective alignment settings."""
        return ", ".join(f"{key}={value}" for key, value in self.to_dict().items())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def _var_ok(self, key: str, value: Any) -> bool:
        try:
            _parse_runtime_value(_field_name_for_key(key), key, value)
            return True
        except ValueError:
            return False

    def apply_runtime_set(self, gcmd: GCodeCommand) -> list[str]:
        """
        Apply ``EDDY_SEEK_SET`` parameters in place.

        Only keys present on the G-code command line are changed.  Returns a
        list of ``field=value`` strings describing what changed.  Raises
        ``gcmd.error`` (``CommandError``) on invalid input.
        """
        params = gcmd.get_command_parameters()
        changes: list[str] = []
        if not params:
            return changes

        gcode_map = _gcode_to_field()
        for key, raw in params.items():
            gcode_key = key.upper()
            field_name = gcode_map.get(gcode_key)
            if field_name is None:
                raise gcmd.error(
                    f"EDDY_SEEK_SET: unknown parameter {key!r} "
                    f"(known: {', '.join(sorted(gcode_map))})"
                )
            try:
                value = _parse_runtime_value(field_name, gcode_key, raw)
            except ValueError as exc:
                raise gcmd.error(
                    f"EDDY_SEEK_SET: invalid {key}={raw!r} ({exc})"
                ) from exc
            setattr(self, field_name, value)
            changes.append(f"{field_name}={value}")
        try:
            _validate(self)
        except ValueError as exc:
            raise gcmd.error(f"EDDY_SEEK_SET: {exc}") from exc
        if changes:
            logger.debug("eddy_seek: runtime config updated: %s", ", ".join(changes))
        return changes


def _seek_field(name: str) -> Field[Any]:
    for spec in fields(SeekConfig):
        if spec.name == name:
            return spec
    raise KeyError(name)


def _gcode_to_field() -> dict[str, str]:
    return {
        spec.metadata["gcode"]: spec.name
        for spec in fields(SeekConfig)
        if "gcode" in spec.metadata
    }


def _field_name_for_key(key: str) -> str:
    gcode_key = key.upper()
    gcode_map = _gcode_to_field()
    if gcode_key in gcode_map:
        return gcode_map[gcode_key]
    if gcode_key.lower() in {spec.name for spec in fields(SeekConfig)}:
        return gcode_key.lower()
    raise ValueError(f"EDDY_SEEK_SET: unknown parameter {key!r}")


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
        return parsed
    raise ValueError(f"EDDY_SEEK_SET: invalid {label}={raw!r}")


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
            if name == "grid_step_x":
                values[name] = config.getfloat(name, values["max_jog_x"] / 2.0)
            elif name == "grid_step_y":
                values[name] = config.getfloat(name, values["max_jog_y"] / 2.0)
            elif spec.metadata.get("bool"):
                values[name] = config.getboolean(name, default)
            elif "enum" in spec.metadata:
                values[name] = config.get(name, default).lower()  # type: ignore[union-attr]
            elif isinstance(default, int):
                values[name] = config.getint(name, default)
            elif isinstance(default, float):
                values[name] = config.getfloat(name, default)
            else:
                values[name] = config.get(name, default)
        if config.getboolean("save_sweep_plots", False) and not _config_option_set(
            config, "save_plots"
        ):
            values["save_plots"] = True  # legacy key; save_plots wins if both set
        cfg = SeekConfig(**values)
        logger.debug("eddy_seek: loaded config %s", cfg.format_seek_config())
        return cfg
    except ValueError as exc:
        raise config.error(f"eddy_seek: {exc}") from exc
