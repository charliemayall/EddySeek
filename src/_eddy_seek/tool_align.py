"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Alignment session logic: seek a single tool or run the full multi-tool sequence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from .common import Offset, Position, yield_to_reactor
from .kconsole import KConsole
from .movement.guard import clear_gcode_offset_xy
from .movement.handler import move_to_xy
from .plot_announce import announce_seek_plot
from .repeated_seek import finalize_repeat_seek, run_repeated_seeks
from .session import (
    ArtifactRunContext,
    SeekHost,
    SeekSession,
    SeekSessionResult,
)
from .strategy import strategy_for
from .tools import Tool, ToolAlignConfig

logger = logging.getLogger(__name__)

_GCODE_STATE_REPEAT = "_eddy_seek_tool_repeat"
# Suggest sensor_x/y tweaks when tool 0's seek offset exceeds this (mm).
_TARGET_SENSOR_OFFSET_FROM_REF = 0.5


@dataclass(frozen=True, slots=True)
class ToolAlignResult:
    status: Literal["ok", "failed"]
    tool0_center: Position | None
    error_message: str | None


def _warn_sensor_position_if_needed(
    sensor: Position,
    seek_offset: Offset,
    *,
    console: KConsole,
) -> None:
    abs_offset = seek_offset.abs_components()
    if max(abs_offset.x, abs_offset.y) <= _TARGET_SENSOR_OFFSET_FROM_REF:
        return
    suggested_x = sensor.x + seek_offset.x
    suggested_y = sensor.y + seek_offset.y
    console.warn(
        f"Tool 0 seek result center: {seek_offset.to_console_str()} "
        f"is significantly different from your configured sensor_x/sensor_y position "
        f"This makes seeks slower for Tool 0, and less accurate for all tools. Consider updating "
        f"(suggested: sensor_x: {suggested_x:.2f}, sensor_y: {suggested_y:.2f}), "
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
    return Position.from_toolhead(toolhead)


def align_tool(
    host: SeekHost,
    gcmd,
    *,
    artifact: ArtifactRunContext | None = None,
    artifact_label: str = "",
    announce_plot: bool = False,
    strategy: str | None = None,
) -> SeekSessionResult:
    """Run XY seek at the current toolhead position."""
    strategy_name = strategy if strategy is not None else host.seek_config.strategy
    strategy_impl = strategy_for(strategy_name)
    return SeekSession(
        host,
        artifact=artifact,
        artifact_label=artifact_label,
    ).run(gcmd, strategy_impl, boundaries=False, announce_plot=announce_plot)


def _seek_tool_repeated(
    host: SeekHost,
    gcmd,
    *,
    repeats: int,
    console: KConsole,
    base_artifact_label: str,
    artifact: ArtifactRunContext | None,
    strategy: str | None = None,
) -> tuple[Offset, SeekSessionResult | None] | None:
    """Run ``repeats`` seeks at the current XY; return mean offset or None on failure."""
    if repeats >= 2 and artifact is None:
        raise RuntimeError("eddy_seek: repeat seeks require artifact run context")

    def run_once(repeat: int) -> SeekSessionResult:
        label = (
            f"{base_artifact_label}_r{repeat}" if repeats >= 2 else base_artifact_label
        )
        return align_tool(
            host,
            gcmd,
            artifact=artifact,
            artifact_label=label,
            announce_plot=repeats >= 2,
            strategy=strategy,
        )

    repeated = run_repeated_seeks(
        host,
        console=console,
        repeats=repeats,
        gcode_state_name=_GCODE_STATE_REPEAT,
        run_once=run_once,
    )
    if repeated is None:
        return None

    if repeats >= 2:
        assert artifact is not None
        finalize_repeat_seek(
            host,
            console,
            repeated,
            artifact=artifact,
            suffix=base_artifact_label,
        )

    return repeated.mean, repeated.last_result


def align_tool_number(
    host: SeekHost,
    tools: ToolAlignConfig,
    gcmd,
    tool_number: int,
    tool0_center: Position | None,
    *,
    console: KConsole,
    load_tool: bool = False,
    artifact: ArtifactRunContext | None = None,
    batch: bool = False,
    repeats: int = 1,
    strategy: str | None = None,
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
        "repeats": repeats,
        "console": console,
        "base_artifact_label": artifact_label,
        "artifact": artifact,
    }

    if tool_number == 0:
        logger.info("eddy_seek: aligning tool 0 (reference)")
        clear_gcode_offset_xy(host.printer)  # caller may have an offset applied
        start = move_to_seek_start_pos(host, tools)
        seek_result = _seek_tool_repeated(host, gcmd, strategy=strategy, **seek_kw)
        if seek_result is None:
            return None, None, "tool 0 alignment failed"

        mean_offset, last_result = seek_result
        center = start + mean_offset
        tool = tools.get_tool(0).mark_calibrated()
        logger.info(
            f"eddy_seek: tool 0 centered at ({center.x:.4f}, {center.y:.4f}) "
            f"seek_offset=({mean_offset.x:.4f}, {mean_offset.y:.4f})"
        )
        console.info(f"Tool 0 reference - {center.to_console_str()}")
        _warn_sensor_position_if_needed(
            tools.sensor_position(), mean_offset, console=console
        )
        if repeats == 1 and last_result is not None:
            announce_seek_plot(
                console,
                plot_path=last_result.plot_path,
                status=last_result.status,
                save_plots=host.seek_config.save_plots,
            )
        return tool, center, None

    if tool0_center is None:
        logger.info(
            f"eddy_seek: align_tool_number tool {tool_number} skipped "
            "(no tool 0 centre)"
        )
        return (
            None,
            None,
            "tool 0 must be aligned before other tools "
            "(Klipper restart clears the reference; run EDDY_SEEK_TOOL TOOL=0 first)",
        )

    if load_tool:
        macro = tools.format_load_macro(tool_number)
        logger.info(f"eddy_seek: loading tool {tool_number} via {macro}")
        tools.run_load_macro(tool_number)
    clear_gcode_offset_xy(host.printer)  # load macro may have added an offset
    toolhead = host.printer.lookup_object("toolhead")
    speed = host.seek_config.jog_speed
    current = Position.from_toolhead(toolhead)
    # DONT GO DIAGONAL, may clip into the "box" where the tools are.
    move_to_xy(toolhead, current.with_x(tool0_center.x), speed, wait=True)
    move_to_xy(toolhead, tool0_center, speed, wait=True)
    yield_to_reactor(host.printer.get_reactor())

    seek_result = _seek_tool_repeated(host, gcmd, strategy=strategy, **seek_kw)
    if seek_result is None:
        return None, tool0_center, f"tool {tool_number} alignment failed"

    mean_offset, last_result = seek_result
    tool = tools.get_tool(tool_number).mark_calibrated(mean_offset)
    logger.info(
        f"eddy_seek: tool {tool_number} offset from tool 0 "
        f"({mean_offset.x:.4f}, {mean_offset.y:.4f})"
    )
    console.info(f"Tool {tool_number} offset - {mean_offset.to_gcode()} mm")
    if repeats == 1 and last_result is not None:
        announce_seek_plot(
            console,
            plot_path=last_result.plot_path,
            status=last_result.status,
            save_plots=host.seek_config.save_plots,
        )
    return tool, tool0_center, None


def align_all_tools(
    host: SeekHost,
    tools: ToolAlignConfig,
    gcmd,
    tool_count: int | None = None,
    *,
    repeats: int = 1,
) -> ToolAlignResult:
    console = KConsole(gcmd, host.seek_config)
    host.console = console
    count = tool_count or tools.tool_count

    logger.info(
        f"eddy_seek: align_all_tools starting {count} tool(s) repeats={repeats}"
    )
    tool0_center: Position | None = None
    artifact = ArtifactRunContext(run_label="tools", write_at=datetime.now())
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
                artifact=artifact,
                batch=True,
                repeats=repeats,
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
        clear_gcode_offset_xy(host.printer)
