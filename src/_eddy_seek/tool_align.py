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
    from klippy.gcode import GCodeDispatch
    from klippy.klippy import Printer

from .config import SeekConfig
from .printer_handler import Tool, ToolAlignConfig
from .session import Position, SeekSession, SeekSessionResult, FrequencySensor

_GCODE_STATE = "eddy_seek_tool_align"


@dataclass
class ToolAlignResult:
    status: Literal["ok", "failed"]
    tool0_center: Position | None
    error_message: str | None


def tool0_center_xy(start_x: float, start_y: float, offset: Position) -> Position:
    return Position(start_x + offset.x, start_y + offset.y)


def _tool_xy(printer: Printer) -> tuple[float, float]:
    pos = printer.lookup_object("toolhead").get_position()
    return pos[0], pos[1]


def move_to_xy(
    printer: Printer, gcode: GCodeDispatch, x: float, y: float, feedrate: float
) -> None:
    gcode.run_script_from_command(f"G90\nG1 X{x:.6f} Y{y:.6f} F{feedrate:.0f}\n")
    printer.lookup_object("toolhead").wait_moves()


def align_tool(
    sensor: FrequencySensor, seek_config: SeekConfig, gcmd
) -> SeekSessionResult:
    """Run XY seek at the current toolhead position."""
    return SeekSession(sensor, seek_config).run(gcmd)


def align_tool_at(
    sensor: FrequencySensor,
    seek_config: SeekConfig,
    gcmd,
    x: float,
    y: float,
) -> SeekSessionResult:
    """Move to absolute XY, then run XY seek."""
    printer = sensor.printer
    gcode = printer.lookup_object("gcode")
    move_to_xy(printer, gcode, x, y, seek_config.jog_speed)
    return align_tool(sensor, seek_config, gcmd)


def align_tool_number(
    sensor: FrequencySensor,
    seek_config: SeekConfig,
    tools: ToolAlignConfig,
    gcmd,
    tool_number: int,
    tool0_center: Position | None,
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
        gcmd.respond_info("EDDY_SEEK_TOOLS: aligning tool 0 (reference)")
        start_x, start_y = _tool_xy(sensor.printer)
        result = align_tool(sensor, seek_config, gcmd)
        if result.status != "ok" or result.offset is None:
            error = result.error_message or "tool 0 alignment failed"
            return None, None, error

        center = tool0_center_xy(start_x, start_y, result.offset)
        tool = Tool(
            tool_number=0,
            offset_x=0.0,
            offset_y=0.0,
            is_calibrated=True,
        )
        gcmd.respond_info(
            f"EDDY_SEEK_TOOLS: tool 0 centred at X={center.x:.4f} Y={center.y:.4f} mm"
        )
        return tool, center, None

    if tool0_center is None:
        return None, None, "tool 0 must be aligned before other tools"

    macro = tools.format_load_macro(tool_number)
    gcmd.respond_info(f"EDDY_SEEK_TOOLS: loading tool {tool_number} ({macro})")
    tools.run_load_macro(tool_number)

    gcmd.respond_info(
        f"EDDY_SEEK_TOOLS: moving tool {tool_number} to tool 0 centre "
        f"X={tool0_center.x:.4f} Y={tool0_center.y:.4f} mm"
    )
    result = align_tool_at(sensor, seek_config, gcmd, tool0_center.x, tool0_center.y)
    if result.status != "ok" or result.offset is None:
        error = result.error_message or f"tool {tool_number} alignment failed"
        return None, tool0_center, error

    tool = Tool(
        tool_number=tool_number,
        offset_x=result.offset.x,
        offset_y=result.offset.y,
        is_calibrated=True,
    )
    gcmd.respond_info(
        f"EDDY_SEEK_TOOLS: tool {tool_number} offset from tool 0: "
        f"X={result.offset.x:+.4f} mm  Y={result.offset.y:+.4f} mm"
    )
    return tool, tool0_center, None


def align_all_tools(
    sensor: FrequencySensor,
    seek_config: SeekConfig,
    tools: ToolAlignConfig,
    gcmd,
    tool_count: int | None = None,
) -> ToolAlignResult:
    printer = sensor.printer
    gcode = printer.lookup_object("gcode")
    count = tool_count or tools.tool_count

    gcmd.respond_info(
        f"EDDY_SEEK_TOOLS: starting - {count} tool(s), "
        f"load macro={tools.load_tool_macro!r}"
    )
    gcode.run_script_from_command(f"SAVE_GCODE_STATE NAME={_GCODE_STATE}")

    tool0_center: Position | None = None
    try:
        for tool_number in range(count):
            tool, tool0_center, error = align_tool_number(
                sensor, seek_config, tools, gcmd, tool_number, tool0_center
            )
            if error is not None:
                gcmd.respond_info(f"EDDY_SEEK_TOOLS ERROR: {error}")
                return ToolAlignResult("failed", tool0_center, error)
            if tool is not None:
                tools.update_tool(tool)

        gcmd.respond_info(f"EDDY_SEEK_TOOLS: done - aligned {count} tool(s)")
        return ToolAlignResult("ok", tool0_center, None)

    finally:
        gcode.run_script_from_command(f"RESTORE_GCODE_STATE NAME={_GCODE_STATE} MOVE=1")
