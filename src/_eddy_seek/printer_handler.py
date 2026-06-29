"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

Tool config, loading, and offset persistence via Klipper configfile autosave.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from klippy.extras.configfile import ConfigWrapper

logger = logging.getLogger(__name__)


class Tool(TypedDict):
    tool_number: int
    offset_x: float
    offset_y: float
    is_calibrated: bool


class ToolAlignConfig:
    def __init__(self, config: ConfigWrapper) -> None:
        self._printer = config.get_printer()
        self.tool_count = config.getint("tool_count", 1, minval=1)
        self.tool_prefix = config.get("tool_prefix", "T")
        self.load_tool_macro = config.get("load_tool_macro_prefix", "T")
        self.sensor_x = config.getfloat("sensor_x", None)
        self.sensor_y = config.getfloat("sensor_y", None)
        if (self.sensor_x is None) != (self.sensor_y is None):
            raise config.error("eddy_seek: set both sensor_x and sensor_y, or neither")
        self.tools = [
            self._load_tool(tool_number) for tool_number in range(self.tool_count)
        ]

    def sensor_position(self) -> tuple[float, float] | None:
        """Configured tool-0 start XY (sensor coil location), or None if unset."""
        if self.sensor_x is None or self.sensor_y is None:
            return None
        return (self.sensor_x, self.sensor_y)

    def _configfile(self):
        return self._printer.lookup_object("configfile")

    def section_name(self, tool_number: int) -> str:
        return f"{self.tool_prefix}{tool_number}"

    def format_load_macro(self, tool_number: int) -> str:
        return f"{self.load_tool_macro}{tool_number}"

    def run_load_macro(self, tool_number: int) -> None:
        macro = self.format_load_macro(tool_number)
        gcode = self._printer.lookup_object("gcode")
        gcode.run_script_from_command(macro)

    def update_tool(self, tool: Tool) -> None:
        for index, existing in enumerate(self.tools):
            if existing["tool_number"] == tool["tool_number"]:
                self.tools[index] = tool
                return
        self.tools.append(tool)

    def save_tools(self) -> None:
        for tool in self.tools:
            self.save_tool(tool)

    def save_tool(self, tool: Tool) -> None:
        configfile = self._configfile()
        section = self.section_name(tool["tool_number"])
        configfile.remove_section(section)
        configfile.set(section, "offset_x", f"{tool['offset_x']:.6f}")
        configfile.set(section, "offset_y", f"{tool['offset_y']:.6f}")
        configfile.set(section, "is_calibrated", tool["is_calibrated"])

    def _load_tool(self, tool_number: int) -> Tool:
        section = self.section_name(tool_number)
        main_config = self._configfile().read_main_config()
        if not main_config.has_section(section):
            return Tool(
                tool_number=tool_number,
                offset_x=0.0,
                offset_y=0.0,
                is_calibrated=False,
            )
        tool_cfg = main_config.getsection(section)
        return Tool(
            tool_number=tool_number,
            offset_x=tool_cfg.getfloat("offset_x", 0.0),
            offset_y=tool_cfg.getfloat("offset_y", 0.0),
            is_calibrated=tool_cfg.getboolean("is_calibrated", False),
        )
