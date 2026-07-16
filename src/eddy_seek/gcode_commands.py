"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Registered G-code commands (single source for Klipper registration and docs).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GcodeCommand:
    name: str
    klipper_desc: str
    doc_signature: str
    doc_description: str


GCODE_COMMANDS: tuple[GcodeCommand, ...] = (
    GcodeCommand(
        "EDDY_SEEK_QUERY",
        "Print current LDC1612 frequency to console",
        "`EDDY_SEEK_QUERY`",
        "Print frequency statistics",
    ),
    GcodeCommand(
        "EDDY_SEEK_RESET",
        "Clear capture buffer before a new alignment measurement",
        "`EDDY_SEEK_RESET`",
        "Manually clear capture buffer (not usually needed)",
    ),
    GcodeCommand(
        "EDDY_SEEK_SET",
        "Temporarily override seek settings until Klipper restart",
        "`EDDY_SEEK_SET [<key>=<value> …]`",
        "Override config until `FIRMWARE_RESTART`. Bare command prints current values "
        "(e.g. `STRATEGY=<enum>`, `TOLERANCE=<float>`).",
    ),
    GcodeCommand(
        "EDDY_SEEK_START",
        "Run XY seek search to find the eddy sensor centre",
        "`EDDY_SEEK_START [STRATEGY=<enum>]`",
        "XY search from current position",
    ),
    GcodeCommand(
        "EDDY_SEEK_ACCURACY",
        "Run seek REPEATS times and report repeatability statistics",
        "`EDDY_SEEK_ACCURACY [REPEATS=<int> MOCK=<0\\|1>]`",
        "Run full seeks (default 3, min 2, max 50) and report σ / max scatter. "
        "`MOCK=1` applies a small random start offset each repeat.",
    ),
    GcodeCommand(
        "EDDY_SEEK_TOOL",
        "Align a single tool on the eddy sensor",
        "`EDDY_SEEK_TOOL TOOL=<int> [REPEATS=<int> STRATEGY=<enum>]`",
        "Align one tool. Load the tool before running. "
        "`REPEATS` seeks are averaged per tool (default 3).<br><br>"
        "⚠️The toolhead must be in a position where it is safe to move X "
        "to tool 0's center, and then Y to tool 0's center.⚠️",
    ),
    GcodeCommand(
        "EDDY_SEEK_APPLY_OFFSET",
        "Apply saved XY offset for a tool via SET_GCODE_OFFSET",
        "`EDDY_SEEK_APPLY_OFFSET [TOOL=<int>]`",
        "DIY only: apply saved XY via `SET_GCODE_OFFSET`. "
        "Errors on INDX (`CHANGE_TOOL` owns apply).",
    ),
)

GCODE_COMMAND_NAMES: frozenset[str] = frozenset(cmd.name for cmd in GCODE_COMMANDS)
