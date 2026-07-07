"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Tool config, loading, and offset persistence via Klipper configfile autosave.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .common import Offset, Position

if TYPE_CHECKING:
    from klippy.extras.configfile import ConfigWrapper, PrinterConfig
    from klippy.klippy import Printer


logger = logging.getLogger(__name__)


def _prefix_and_num_to_section(prefix: str, tool_number: int) -> str:
    return f"{prefix}{tool_number}"


@dataclass
class Tool:
    tool_number: int
    offset: Offset
    manual_offset: Offset
    is_calibrated: bool

    @property
    def effective_offset(self) -> Offset:
        return self.offset + self.manual_offset

    @staticmethod
    def create_default(tool_number: int) -> Tool:
        return Tool(
            tool_number=tool_number,
            offset=Offset.zero(),
            manual_offset=Offset.zero(),
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
            offset=Offset(
                x=section.getfloat("offset_x", 0.0),
                y=section.getfloat("offset_y", 0.0),
            ),
            manual_offset=Offset(
                x=section.getfloat("manual_adjust_x", 0.0),
                y=section.getfloat("manual_adjust_y", 0.0),
            ),
            is_calibrated=section.getboolean("is_calibrated", False),
        )

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

    def mark_calibrated(self, offset: Offset | None = None) -> Tool:
        """Return a copy marked calibrated with the given seek offset."""
        if offset is None:
            offset = Offset.zero()
        return Tool(
            tool_number=self.tool_number,
            offset=offset,
            manual_offset=self.manual_offset,
            is_calibrated=True,
        )


class ToolAlignConfig:
    def __init__(self, config: ConfigWrapper) -> None:
        self._printer = config.get_printer()
        self.tool_count = config.getint("tool_count", 1, minval=1)
        self.tool_prefix = config.get("tool_prefix", "es_T")
        self.load_tool_macro = config.get("load_tool_macro_prefix", "T")
        self.sensor_x = config.getfloat("sensor_x")
        self.sensor_y = config.getfloat("sensor_y")
        sensor_z_raw = config.get("sensor_z", None)
        self.sensor_z = (
            config.getfloat("sensor_z") if sensor_z_raw is not None else None
        )
        main_config = self._configfile().read_main_config()
        self.tools = [
            self._load_tool_or_default(main_config, tool_number)
            for tool_number in range(self.tool_count)
        ]
        sensor_z_text = f"{self.sensor_z:.4f}" if self.sensor_z is not None else "unset"
        logger.info(
            f"eddy_seek: tools config tool_count={self.tool_count} "
            f"sensor=({self.sensor_x:.4f}, {self.sensor_y:.4f}, {sensor_z_text}) "
            f"prefix={self.tool_prefix!r} load_macro={self.load_tool_macro!r}"
        )

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
        macro = self.format_load_macro(tool_number)
        logger.info(f"eddy_seek: running load macro {macro!r}")
        gcode = self._printer.lookup_object("gcode")
        gcode.run_script_from_command(macro)

    def update_tool(self, tool: Tool) -> None:
        self.tools[tool.tool_number] = tool

    def save_tools(self) -> None:
        for tool in self.tools:
            self.save_tool(tool)

    def save_tool(self, tool: Tool) -> None:
        logger.info(
            f"eddy_seek: staging tool {tool.tool_number} "
            f"offset=({tool.offset.x:.6f}, {tool.offset.y:.6f}) "
            f"calibrated={tool.is_calibrated}"
        )
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
        raise ValueError(
            f"Tool {tool_number} is not calibrated, and you are trying to apply an offset."
        )
    eff = tool.effective_offset
    logger.info(
        f"eddy_seek: applying tool {tool_number} effective offset "
        f"({eff.x:.6f}, {eff.y:.6f})"
    )
    gcode = printer.lookup_object("gcode")
    gcode.run_script_from_command(f"SET_GCODE_OFFSET X={eff.x:.6f} Y={eff.y:.6f}")
    return tool
