"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Alignment session logic: seek a single tool or run the full multi-tool sequence.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .common import Offset, Position
from .kconsole import KConsole, console_for_gcmd
from .movement.guard import clear_gcode_offset_xy
from .movement.handler import move_to_xy
from .session import SeekHost, SeekSession, SeekSessionResult
from .strategy import strategy_for
from .tools import Tool, ToolAlignConfig

logger = logging.getLogger(__name__)

_GCODE_STATE = "_eddy_seek_tool_align"
# Suggest sensor_x/y tweaks when tool 0's seek offset exceeds this (mm).
_TARGET_SENSOR_OFFSET_FROM_REF = 0.5


@dataclass(frozen=True, slots=True)
class ToolAlignResult:
    status: Literal["ok", "failed"]
    tool0_center: Position | None
    error_message: str | None


def _warn_sensor_position_if_needed(
    tools: ToolAlignConfig,
    seek_offset: Offset,
    *,
    console: KConsole,
) -> None:
    abs_offset = seek_offset.abs_components()
    if max(abs_offset.x, abs_offset.y) <= _TARGET_SENSOR_OFFSET_FROM_REF:
        return
    suggested_x = tools.sensor_x + seek_offset.x
    suggested_y = tools.sensor_y + seek_offset.y
    console.warn(
        f"Tool 0 seek result center: X={seek_offset.x:+.4f} Y={seek_offset.y:+.4f} mm "
        f"is signifcantly different from your configured sensor_x/sensor_y position "
        f"This makes seeks slower for Tool 0, and less accurate for all tools. Consider updating "
        f"(suggested: sensor_x: {suggested_x:.4f}, sensor_y: {suggested_y:.4f}), "
        f"then FIRMWARE_RESTART"
    )


def move_to_seek_start_pos(
    host: SeekHost,
    tools: ToolAlignConfig,
) -> Position:
    """
    Decide where tool 0's seek begins.

    Tool 0 starts at the configured sensor coil XY so unattended
    ``EDDY_SEEK_TOOLS`` does not rely on the caller's current position.
    """
    sensor = tools.sensor_position()
    logger.info(f"eddy_seek: moving tool 0 to sensor position {sensor.to_gcode()}")
    toolhead = host.printer.lookup_object("toolhead")
    move_to_xy(toolhead, sensor, host.seek_config.jog_speed, wait=True)
    return Position.from_toolhead(host.printer)


def _announce_plot_result(
    host: SeekHost,
    console: KConsole,
    result: SeekSessionResult,
) -> None:
    if result.plot_path is not None:
        console.plot_saved(result.plot_path)
    elif host.seek_config.save_plots and result.status == "ok":
        console.warn(
            "save_plots is enabled but no plot was written (is plotly installed?)"
        )


def align_tool(
    host: SeekHost,
    gcmd,
    *,
    run_id: str | None = None,
    run_label: str = "run",
    artifact_label: str = "",
    artifact_write_at: datetime | None = None,
) -> SeekSessionResult:
    """Run XY seek at the current toolhead position."""
    strategy = strategy_for(host.seek_config.strategy)
    return SeekSession(
        host,
        run_id=run_id,
        run_label=run_label,
        artifact_label=artifact_label,
        artifact_write_at=artifact_write_at,
    ).run(gcmd, strategy, boundaries=False, announce_plot=False)


def align_tool_at(
    host: SeekHost,
    gcmd,
    start_pos: Position,
    *,
    run_id: str | None = None,
    run_label: str = "run",
    artifact_label: str = "",
    artifact_write_at: datetime | None = None,
) -> SeekSessionResult:
    """Move to absolute XY, then run XY seek."""
    toolhead = host.printer.lookup_object("toolhead")
    move_to_xy(toolhead, start_pos, host.seek_config.jog_speed, wait=True)
    return align_tool(
        host,
        gcmd,
        run_id=run_id,
        run_label=run_label,
        artifact_label=artifact_label,
        artifact_write_at=artifact_write_at,
    )


def align_tool_number(
    host: SeekHost,
    tools: ToolAlignConfig,
    gcmd,
    tool_number: int,
    tool0_center: Position | None,
    *,
    console: KConsole,
    load_tool: bool = False,
    run_id: str | None = None,
    run_label: str = "run",
    artifact_write_at: datetime | None = None,
    batch: bool = False,
) -> tuple[Tool | None, Position | None, str | None]:
    """
    Align one tool and return its updated Tool record.

    Tool 0 establishes the reference centre.  Later tools are moved to that
    centre, then seeked.  The seek offset is the inter-tool XY offset.

    When ``load_tool`` is true (``EDDY_SEEK_TOOLS``), the load macro runs first.
    ``EDDY_SEEK_TOOL`` leaves ``load_tool`` false so the caller loads the tool.
    """
    if tool_number < 0 or tool_number >= tools.tool_count:
        logger.info(
            f"eddy_seek: align_tool_number rejected tool {tool_number} (range 0..{tools.tool_count - 1})",
        )
        return (
            None,
            tool0_center,
            f"tool {tool_number} out of range 0..{tools.tool_count - 1}",
        )

    artifact_label = f"tools_t{tool_number}" if batch else f"tool{tool_number}"
    seek_kw = {
        "run_id": run_id,
        "run_label": run_label,
        "artifact_label": artifact_label,
        "artifact_write_at": artifact_write_at,
    }

    if tool_number == 0:
        logger.info("eddy_seek: aligning tool 0 (reference)")
        clear_gcode_offset_xy(host.printer)
        start = move_to_seek_start_pos(host, tools)
        result = align_tool(host, gcmd, **seek_kw)
        if result.status != "ok" or result.offset is None:
            error = result.error_message or "tool 0 alignment failed"
            return None, None, error

        center = start + result.offset
        tool = tools.get_tool(0).mark_calibrated()
        logger.info(
            f"eddy_seek: tool 0 centered at ({center.x:.4f}, {center.y:.4f}) "
            f"seek_offset=({result.offset.x:.4f}, {result.offset.y:.4f})"
        )
        console.info(f"Tool 0 reference - X={center.x:.4f} Y={center.y:.4f} mm")
        _warn_sensor_position_if_needed(tools, result.offset, console=console)
        _announce_plot_result(host, console, result)
        return tool, center, None

    if tool0_center is None:
        logger.info(
            f"eddy_seek: align_tool_number tool {tool_number} skipped "
            "(no tool 0 centre)"
        )
        return None, None, "tool 0 must be aligned before other tools"

    if load_tool:
        macro = tools.format_load_macro(tool_number)
        logger.info(f"eddy_seek: loading tool {tool_number} via {macro}")
        tools.run_load_macro(tool_number)
    clear_gcode_offset_xy(host.printer)

    result = align_tool_at(host, gcmd, tool0_center, **seek_kw)
    if result.status != "ok" or result.offset is None:
        error = result.error_message or f"tool {tool_number} alignment failed"
        return None, tool0_center, error

    tool = tools.get_tool(tool_number).mark_calibrated(result.offset)
    logger.info(
        f"eddy_seek: tool {tool_number} offset from tool 0 "
        f"({result.offset.x:.4f}, {result.offset.y:.4f})"
    )
    console.info(f"Tool {tool_number} offset - {result.offset.to_gcode()} mm")
    _announce_plot_result(host, console, result)
    return tool, tool0_center, None


def align_all_tools(
    host: SeekHost,
    tools: ToolAlignConfig,
    gcmd,
    tool_count: int | None = None,
) -> ToolAlignResult:
    console = console_for_gcmd(gcmd, host.seek_config)
    host.console = console
    printer = host.printer
    gcode = printer.lookup_object("gcode")
    count = tool_count or tools.tool_count

    logger.info(f"eddy_seek: align_all_tools starting {count} tool(s)")
    gcode.run_script_from_command(f"SAVE_GCODE_STATE NAME={_GCODE_STATE}")

    tool0_center: Position | None = None
    run_id = uuid.uuid4().hex[:8]
    write_at = datetime.now()
    try:
        for tool_number in range(count):
            if tool_number == 0:
                console.entry(f"Aligning tool 1 of {count}…")
            else:
                console.info(f"Aligning tool {tool_number + 1} of {count}…")
            tool, tool0_center, error = align_tool_number(
                host,
                tools,
                gcmd,
                tool_number,
                tool0_center,
                console=console,
                load_tool=True,
                run_id=run_id,
                run_label="tools",
                artifact_write_at=write_at,
                batch=True,
            )
            if error is not None:
                logger.info(
                    f"eddy_seek: align_all_tools failed on tool {tool_number}: {error}"
                )
                console.error(f"Tool {tool_number} alignment failed: {error}")
                return ToolAlignResult("failed", tool0_center, error)
            if tool is not None:
                tools.update_tool(tool)

        logger.info(f"eddy_seek: align_all_tools done {count} tool(s)")
        console.exit(f"{count} tools aligned - run SAVE_CONFIG to persist")
        return ToolAlignResult("ok", tool0_center, None)

    finally:
        gcode.run_script_from_command(f"RESTORE_GCODE_STATE NAME={_GCODE_STATE} MOVE=1")
        clear_gcode_offset_xy(host.printer)
