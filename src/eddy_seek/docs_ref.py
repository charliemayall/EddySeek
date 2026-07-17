"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Generate USER_GUIDE reference tables from code metadata.
"""

from __future__ import annotations

from dataclasses import Field, fields
from typing import Any

from .config import SeekConfig
from .gcode_commands import GCODE_COMMANDS
from .tools.registry import toolchanger_types

_COMBINED_SEEK_FIELD_NAMES = frozenset({"max_jog_x", "max_jog_y"})

_SWEEP_FIELD_NAMES = frozenset(
    {
        "sweep_coarse_speed",
        "sweep_fine_speed",
        "sweep_overscan",
        "sweep_cross_offset",
        "coarse_phases",
        "coarse_cross_passes",
        "fine_shrink",
        "min_sweep_samples",
        "sweep_arc_resolution",
    }
)

_TOOL_SECTION_ROWS: tuple[tuple[str, str, str], ...] = (
    ("`sensor_type`", "_(required)_", "`ldc1612`"),
    (
        "`i2c_address`",
        "_(optional)_",
        "LDC1612 I2C address; Klipper defaults to `42` (`0x2a`) when omitted",
    ),
    ("`i2c_mcu`", "_(required)_", "MCU name, e.g. `mcu`"),
    ("`i2c_bus`", "_(required)_", "I2C bus, e.g. `i2c1`"),
    (
        "`toolchanger_type`",
        "`diy`",
        f"`{'` or `'.join(sorted(toolchanger_types()))}` - INDX uses `CHANGE_TOOL` and `TOOL_POSITIONS`",
    ),
    ("`tool_prefix`", "`es_T`", "Prefix for saved offset sections (`es_T1`, …)"),
    (
        "`sensor_z`",
        "_(optional)_",
        "Machine Z for seek commands; errors if outside `[sensor_z, sensor_z + 0.25]` mm",
    ),
)


def _seek_field(name: str) -> Field[Any]:
    for spec in fields(SeekConfig):
        if spec.name == name:
            return spec
    raise KeyError(name)


def _display_default(spec: Field[Any], cfg: SeekConfig) -> str:
    if spec.name == "result_folder":
        return f"`{spec.default}`"
    value = getattr(cfg, spec.name)
    if spec.metadata.get("bool"):
        return "`True`" if value else "`False`"
    if "enum" in spec.metadata:
        return f"`{value}`"
    if spec.metadata.get("speed"):
        return _fmt_num(value / 60.0)
    if isinstance(value, int):
        return f"`{value}`"
    if isinstance(value, float):
        return _fmt_num(value)
    return f"`{value}`"


def _fmt_num(value: float) -> str:
    text = f"{value:g}"
    return f"`{text}`"


def _format_default_pair(left: str, right: str) -> str:
    cfg = SeekConfig()
    left_val = _display_default(_seek_field(left), cfg)
    right_val = _display_default(_seek_field(right), cfg)
    if left_val == right_val:
        return left_val
    return f"{left_val} / {right_val}"


def _seek_rows(*, sweep: bool) -> list[tuple[str, str, str]]:
    cfg = SeekConfig()
    rows: list[tuple[str, str, str]] = []
    for spec in fields(SeekConfig):
        if spec.name in _COMBINED_SEEK_FIELD_NAMES:
            continue
        if spec.name in _SWEEP_FIELD_NAMES:
            if not sweep:
                continue
        elif sweep:
            continue
        if "doc" not in spec.metadata:
            continue
        rows.append(
            (
                f"`{spec.name}`",
                _display_default(spec, cfg),
                spec.metadata["doc"],
            )
        )
    return rows


def scroll_of_truth(rows: list[tuple[str, str, str]]) -> str:
    """Inscribe rows into the sacred markdown table."""
    lines = [
        "| Option | Default | Description |",
        "| ------ | ------- | ----------- |",
    ]
    for option, default, description in rows:
        lines.append(f"| {option} | {default} | {description} |")
    return "\n".join(lines)


def _combined_seek_rows() -> list[tuple[str, str, str]]:
    return [
        (
            "`max_jog_x` / `max_jog_y`",
            _format_default_pair("max_jog_x", "max_jog_y"),
            _seek_field("max_jog_x").metadata["doc"],
        )
    ]


def seek_config_main_table() -> str:
    rows = list(_TOOL_SECTION_ROWS)
    rows.extend(_combined_seek_rows())
    rows.extend(_seek_rows(sweep=False))
    return scroll_of_truth(rows)


def seek_config_sweep_table() -> str:
    return scroll_of_truth(_seek_rows(sweep=True))


def gcode_commands_table() -> str:
    lines = [
        "| Command | Description |",
        "| ------- | ----------- |",
    ]
    lines.extend(
        f"| {cmd.doc_signature} | {cmd.doc_description} |" for cmd in GCODE_COMMANDS
    )
    return "\n".join(lines)


def generated_marker(name: str, *, begin: bool) -> str:
    tag = "BEGIN" if begin else "END"
    return f"<!-- {tag}:{name} -->"
