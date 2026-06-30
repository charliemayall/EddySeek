"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

Alignment session logic: seek a single tool or run the full multi-tool sequence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from klippy.klippy import Printer

from .common import Position
from .session import SeekSession, SeekSessionResult, SeekHost
from .tools import Tool, ToolAlignConfig

_GCODE_STATE = "eddy_seek_tool_align"


@dataclass(frozen=True, slots=True)
class ToolAlignResult:
    status: Literal["ok", "failed"]
    tool0_center: Position | None
    error_message: str | None


def tool0_center_xy(start_x: float, start_y: float, offset: Position) -> Position:
    return Position(start_x + offset.x, start_y + offset.y)


def _tool_xy(printer: Printer) -> tuple[float, float]:
    pos = printer.lookup_object("toolhead").get_position()
    return pos[0], pos[1]


def move_to_xy(printer: Printer, x: float, y: float, feedrate: float) -> None:
    toolhead = printer.lookup_object("toolhead")
    toolhead.manual_move([x, y], feedrate / 60.0)
    toolhead.wait_moves()


def move_to_seek_start_pos(
    host: SeekHost,
    tools: ToolAlignConfig,
    gcmd,
    label: str,
) -> tuple[float, float]:
    """
    Decide where tool 0's seek begins.

    Tool 0 starts at the configured sensor coil XY so unattended
    ``EDDY_SEEK_TOOLS`` does not rely on the caller's current position.
    """
    sensor_xy = tools.sensor_position()
    gcmd.respond_info(
        f"{label}: moving tool 0 to sensor position {sensor_xy.to_gcode()} mm"
    )
    move_to_xy(host.printer, *sensor_xy.seq, host.seek_config.jog_speed)

    return _tool_xy(host.printer)


def align_tool(host: SeekHost, gcmd) -> SeekSessionResult:
    """Run XY seek at the current toolhead position."""
    return SeekSession(host).run(gcmd)


def align_tool_at(
    host: SeekHost,
    gcmd,
    position: Position,
) -> SeekSessionResult:
    """Move to absolute XY, then run XY seek."""
    move_to_xy(host.printer, *position.seq, host.seek_config.jog_speed)
    return align_tool(host, gcmd)


def align_tool_number(
    host: SeekHost,
    tools: ToolAlignConfig,
    gcmd,
    tool_number: int,
    tool0_center: Position | None,
    *,
    label: str = "EDDY_SEEK_TOOLS",
) -> tuple[Tool | None, Position | None, str | None]:
    """
    Align one tool and return its updated Tool record.

    Tool 0 establishes the reference centre.  Later tools are loaded, moved to
    that centre, then seeked.  The seek offset is the inter-tool XY offset.
    """
    if tool_number < 0 or tool_number >= tools.tool_count:
        return (
            None,
            tool0_center,
            f"tool {tool_number} out of range 0..{tools.tool_count - 1}",
        )

    if tool_number == 0:
        gcmd.respond_info(f"{label}: aligning tool 0 (reference)")
        start_x, start_y = move_to_seek_start_pos(host, tools, gcmd, label)
        result = align_tool(host, gcmd)
        if result.status != "ok" or result.offset is None:
            error = result.error_message or "tool 0 alignment failed"
            return None, None, error

        center = tool0_center_xy(start_x, start_y, result.offset)
        tool = tools.get_tool(0).mark_calibrated()
        gcmd.respond_info(
            f"{label}: tool 0 centred at X={center.x:.4f} Y={center.y:.4f} mm"
        )
        return tool, center, None

    if tool0_center is None:
        return None, None, "tool 0 must be aligned before other tools"

    macro = tools.format_load_macro(tool_number)
    gcmd.respond_info(f"{label}: loading tool {tool_number} ({macro})")
    tools.run_load_macro(tool_number)

    gcmd.respond_info(
        f"{label}: moving tool {tool_number} to tool 0 centre "
        f"{tool0_center.to_gcode()} mm"
    )
    result = align_tool_at(host, gcmd, tool0_center)
    if result.status != "ok" or result.offset is None:
        error = result.error_message or f"tool {tool_number} alignment failed"
        return None, tool0_center, error

    tool = tools.get_tool(tool_number).mark_calibrated(result.offset.x, result.offset.y)
    gcmd.respond_info(
        f"{label}: tool {tool_number} offset from tool 0: {result.offset.to_gcode()} mm"
    )
    return tool, tool0_center, None


def align_all_tools(
    host: SeekHost,
    tools: ToolAlignConfig,
    gcmd,
    tool_count: int | None = None,
) -> ToolAlignResult:
    label = "EDDY_SEEK_TOOLS"
    printer = host.printer
    gcode = printer.lookup_object("gcode")
    count = tool_count or tools.tool_count

    gcmd.respond_info(
        f"{label}: starting - {count} tool(s), load macro={tools.load_tool_macro!r}"
    )
    gcode.run_script_from_command(f"SAVE_GCODE_STATE NAME={_GCODE_STATE}")

    tool0_center: Position | None = None
    try:
        for tool_number in range(count):
            tool, tool0_center, error = align_tool_number(
                host,
                tools,
                gcmd,
                tool_number,
                tool0_center,
                label=label,
            )
            if error is not None:
                gcmd.respond_info(f"{label} ERROR: {error}")
                return ToolAlignResult("failed", tool0_center, error)
            if tool is not None:
                tools.update_tool(tool)

        gcmd.respond_info(f"{label}: done - aligned {count} tool(s)")
        return ToolAlignResult("ok", tool0_center, None)

    finally:
        gcode.run_script_from_command(f"RESTORE_GCODE_STATE NAME={_GCODE_STATE} MOVE=1")
