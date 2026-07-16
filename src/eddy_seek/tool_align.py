"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Alignment session logic: seek a single tool against the eddy sensor.
"""

from __future__ import annotations

import logging

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
from .tools.protocol import ToolAlignConfig, ToolProtocol

logger = logging.getLogger(__name__)

_GCODE_STATE_REPEAT = "eddy_seek_tool_repeat"


def move_to_seek_start_pos(host: SeekHost) -> Position:
    """Return current toolhead XY as tool 0 seek start."""
    toolhead = host.printer.lookup_object("toolhead")
    start = Position.from_toolhead(toolhead)
    logger.info(f"eddy_seek: tool 0 seek start {start.to_gcode()}")
    return start


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
    artifact: ArtifactRunContext | None = None,
    repeats: int = 1,
    strategy: str | None = None,
) -> tuple[ToolProtocol | None, Position | None, str | None]:
    """
    Align one tool and return its updated Tool record.

    Tool 0 establishes the reference centre.  Later tools are moved to that
    centre, then seeked.  The seek offset is the inter-tool XY offset.
    """
    if tool_number < 0:
        logger.info(
            f"eddy_seek: align_tool_number rejected tool {tool_number} (negative)",
        )
        return (
            None,
            tool0_center,
            f"tool {tool_number} out of range",
        )

    artifact_label = f"tool{tool_number}"
    seek_kw = {
        "repeats": repeats,
        "console": console,
        "base_artifact_label": artifact_label,
        "artifact": artifact,
    }

    if tool_number == 0:
        logger.info("eddy_seek: aligning tool 0 (reference)")
        clear_gcode_offset_xy(host.printer)
        start = move_to_seek_start_pos(host)
        seek_result = _seek_tool_repeated(host, gcmd, strategy=strategy, **seek_kw)
        if seek_result is None:
            return None, None, "tool 0 alignment failed"

        mean_offset, last_result = seek_result
        center = start + mean_offset
        try:
            tool = tools.get_tool(0).mark_calibrated()
        except IndexError as exc:
            return None, None, str(exc)
        logger.info(
            f"eddy_seek: tool 0 centered at ({center.x:.4f}, {center.y:.4f}) "
            f"seek_offset=({mean_offset.x:.4f}, {mean_offset.y:.4f})"
        )
        console.info(f"Tool 0 reference - {center.to_console_str()}")
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

    clear_gcode_offset_xy(host.printer)
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
    try:
        tool = tools.get_tool(tool_number).mark_calibrated(mean_offset)
    except IndexError as exc:
        return None, tool0_center, str(exc)
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
