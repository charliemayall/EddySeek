"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.

Tool config, loading, and offset persistence via Klipper configfile autosave.
"""

from __future__ import annotations

from dataclasses import dataclass
import dataclasses
import logging
from typing import TYPE_CHECKING, Optional

from .common import Position

if TYPE_CHECKING:
    from klippy.extras.configfile import ConfigWrapper, PrinterConfig
    from klippy.klippy import Printer


logger = logging.getLogger(__name__)


def _prefix_and_num_to_section(prefix: str, tool_number: int) -> str:
    return f"{prefix}{tool_number}"


@dataclass
class Tool:
    tool_number: int
    offset: Position
    manual_offset: Position
    is_calibrated: bool

    @property
    def effective_offset(self) -> Position:
        return Position(
            x=self.offset.x + self.manual_offset.x,
            y=self.offset.y + self.manual_offset.y,
        )

    @staticmethod
    def create_default(tool_number: int) -> Tool:
        return Tool(
            tool_number=tool_number,
            offset=Position(0.0, 0.0),
            manual_offset=Position(0.0, 0.0),
            is_calibrated=False,
        )

    @staticmethod
    def load(config: ConfigWrapper, prefix: str, tool_number: int) -> Tool:
        """
        Load from a config section.

        [{prefix}{tool_number}]
        offset_x: 0.000000
        offset_y: 0.000000
        ...
        """
        section_name = _prefix_and_num_to_section(prefix, tool_number)
        if not config.has_section(section_name):
            raise config.error(f"tool {tool_number} section not found")
        section = config.getsection(section_name)
        return Tool(
            tool_number=tool_number,
            offset=Position(
                x=section.getfloat("offset_x", 0.0),
                y=section.getfloat("offset_y", 0.0),
            ),
            manual_offset=Position(
                x=section.getfloat("manual_adjust_x", 0.0),
                y=section.getfloat("manual_adjust_y", 0.0),
            ),
            is_calibrated=section.getboolean("is_calibrated", False),
        )

    def set_offset(self, x: Optional[float] = None, y: Optional[float] = None) -> None:
        if x is None:
            x = self.offset.x
        if y is None:
            y = self.offset.y
        self.offset = Position(x, y)

    def save(self, config: PrinterConfig, prefix: str) -> None:
        """Write this tool's fields into a config section."""
        section = _prefix_and_num_to_section(prefix, self.tool_number)
        for key, val in [
            ("offset_x", self.offset.x),
            ("offset_y", self.offset.y),
            ("manual_adjust_x", self.manual_offset.x),
            ("manual_adjust_y", self.manual_offset.y),
        ]:
            config.set(section, key, f"{val:.6f}")
        config.set(section, "is_calibrated", self.is_calibrated)

    def to_dict(self) -> dict[str, float | int | bool]:
        return dataclasses.asdict(self)

    def mark_calibrated(self, x: float = 0.0, y: float = 0.0) -> Tool:
        """Return a copy marked calibrated with the given seek offset."""
        return Tool(
            tool_number=self.tool_number,
            offset=Position(x, y),
            manual_offset=self.manual_offset,
            is_calibrated=True,
        )


class ToolAlignConfig:
    def __init__(self, config: ConfigWrapper) -> None:
        self._printer = config.get_printer()
        self.tool_count = config.getint("tool_count", 1, minval=1)
        self.tool_prefix = config.get("tool_prefix", "T")
        self.load_tool_macro = config.get("load_tool_macro_prefix", "T")
        self.sensor_x = config.getfloat("sensor_x")
        self.sensor_y = config.getfloat("sensor_y")
        main_config = self._configfile().read_main_config()
        self.tools = [
            self._load_tool_or_default(main_config, tool_number)
            for tool_number in range(self.tool_count)
        ]

    def sensor_position(self) -> Position:
        """Configured tool-0 start XY (sensor coil location)."""
        return Position(self.sensor_x, self.sensor_y)

    def section_name(self, tool_number: int) -> str:
        return f"{self.tool_prefix}{tool_number}"

    def get_tool(self, tool_number: int) -> Tool:
        if tool_number < 0 or tool_number >= self.tool_count:
            raise IndexError(
                f"tool {tool_number} out of range 0..{self.tool_count - 1}"
            )
        return self.tools[tool_number]

    def format_load_macro(self, tool_number: int) -> str:
        return f"{self.load_tool_macro}{tool_number}"

    def run_load_macro(self, tool_number: int) -> None:
        gcode = self._printer.lookup_object("gcode")
        gcode.run_script_from_command(self.format_load_macro(tool_number))

    def update_tool(self, tool: Tool) -> None:
        self.tools[tool.tool_number] = tool

    def save_tools(self) -> None:
        for tool in self.tools:
            self.save_tool(tool)

    def save_tool(self, tool: Tool) -> None:
        configfile = self._configfile()
        configfile.remove_section(self.section_name(tool.tool_number))
        tool.save(configfile, self.tool_prefix)

    def _configfile(self):
        return self._printer.lookup_object("configfile")

    def _load_tool_or_default(self, main_config, tool_number: int) -> Tool:
        if not main_config.has_section(self.section_name(tool_number)):
            return Tool.create_default(tool_number)
        return Tool.load(main_config, self.tool_prefix, tool_number)


def apply_tool_offset(
    tools: ToolAlignConfig,
    printer: Printer,
    tool_number: int,
) -> Tool:
    """Apply a calibrated tool's stored XY offset via ``SET_GCODE_OFFSET``."""
    try:
        tool = tools.get_tool(tool_number)
    except IndexError as exc:
        raise ValueError(str(exc)) from exc
    if not tool.is_calibrated:
        raise ValueError(f"tool {tool_number} is not calibrated")
    eff = tool.effective_offset
    gcode = printer.lookup_object("gcode")
    gcode.run_script_from_command(f"SET_GCODE_OFFSET X={eff.x:.6f} Y={eff.y:.6f}")
    return tool
