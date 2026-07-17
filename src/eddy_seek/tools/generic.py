"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..common import Offset
from .bootstrap import log_kit_startup, read_config_context, run_detection
from .protocol import ToolAlignConfig, ToolProtocol, ToolRecord, calibrated_offset
from .registry import DETECTION_ORDER, toolchanger_types

if TYPE_CHECKING:
    from klippy.configfile import ConfigWrapper, PrinterConfig

logger = logging.getLogger(__name__)


def _section_name(prefix: str, tool_number: int) -> str:
    return f"{prefix}{tool_number}"


def _tool_numbers_from_sections(
    main_config: ConfigWrapper, tool_prefix: str
) -> list[int]:
    pattern = re.compile(rf"^{re.escape(tool_prefix)}(\d+)$")
    numbers: list[int] = []
    for section_cfg in main_config.get_prefix_sections(tool_prefix):
        match = pattern.match(section_cfg.get_name())
        if match:
            numbers.append(int(match.group(1)))
    return numbers


def _tools_from_discovered_sections(
    main_config: ConfigWrapper, tool_prefix: str
) -> list[GenericTool]:
    numbers = _tool_numbers_from_sections(main_config, tool_prefix)
    if not numbers:
        return []
    return [
        GenericToolAlignConfig._load_tool_or_default(main_config, tool_prefix, n)
        for n in range(max(numbers) + 1)
    ]


@dataclass
class GenericTool(ToolRecord):
    manual_offset: Offset

    @property
    def effective_offset(self) -> Offset:
        return self.offset + self.manual_offset

    @classmethod
    def create_default(cls, tool_number: int) -> GenericTool:
        return cls(
            tool_number=tool_number,
            offset=Offset.zero(),
            manual_offset=Offset.zero(),
            is_calibrated=False,
        )

    @staticmethod
    def load(config: ConfigWrapper, prefix: str, tool_number: int) -> GenericTool:
        """
        Load from a config section.

        [{prefix}{tool_number}]
        offset_x: 0.000000
        offset_y: 0.000000
        ...
        """
        section_name = _section_name(prefix, tool_number)
        if not config.has_section(section_name):
            raise config.error(f"tool {tool_number} section not found")
        section = config.getsection(section_name)
        return GenericTool(
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
        section = _section_name(prefix, self.tool_number)
        for key, val in [
            ("offset_x", self.offset.x),
            ("offset_y", self.offset.y),
            ("manual_adjust_x", self.manual_offset.x),
            ("manual_adjust_y", self.manual_offset.y),
        ]:
            config.set(section, key, f"{val:.6f}")
        config.set(section, "is_calibrated", str(self.is_calibrated))

    def mark_calibrated(self, offset: Offset | None = None) -> GenericTool:
        """Return a copy marked calibrated with the given seek offset."""
        return dataclasses.replace(
            self,
            offset=calibrated_offset(offset),
            is_calibrated=True,
        )


class GenericToolAlignConfig(ToolAlignConfig):
    """Generic toolchanger with config section persistence."""

    def __init__(
        self,
        *,
        printer,
        tool_count: int,
        tools: list[GenericTool],
        sensor_z: float | None,
        tool_prefix: str,
    ) -> None:
        super().__init__(
            printer=printer,
            tool_count=tool_count,
            tools=tools,
            sensor_z=sensor_z,
            toolchanger_type="generic",
        )
        self.tool_prefix = tool_prefix

    @classmethod
    def _from_config(cls, config: ConfigWrapper) -> GenericToolAlignConfig:
        tool_prefix = config.get("tool_prefix", "es_T")
        sensor_z, printer, main_config = read_config_context(config)
        types = toolchanger_types()
        run_detection("generic", main_config, types, DETECTION_ORDER)

        tools = _tools_from_discovered_sections(main_config, tool_prefix)
        tool_count = len(tools)
        log_kit_startup(
            toolchanger_type="generic",
            tool_count=tool_count,
            sensor_z=sensor_z,
            extra=f"prefix={tool_prefix!r}",
        )
        return cls(
            printer=printer,
            tool_count=tool_count,
            tools=tools,
            sensor_z=sensor_z,
            tool_prefix=tool_prefix,
        )

    def get_tool(self, tool_number: int) -> ToolProtocol:
        if tool_number < 0:
            raise IndexError(f"tool {tool_number} out of range")
        while len(self.tools) <= tool_number:
            self.tools.append(GenericTool.create_default(len(self.tools)))
        self.tool_count = len(self.tools)
        return self.tools[tool_number]

    @staticmethod
    def _load_tool_or_default(
        main_config: ConfigWrapper, tool_prefix: str, tool_number: int
    ) -> GenericTool:
        section = f"{tool_prefix}{tool_number}"
        if not main_config.has_section(section):
            return GenericTool.create_default(tool_number)
        return GenericTool.load(main_config, tool_prefix, tool_number)

    def section_name(self, tool_number: int) -> str:
        return f"{self.tool_prefix}{tool_number}"

    def tool_status_key(self, tool_number: int) -> str:
        return self.section_name(tool_number)

    def kit_trace(self) -> dict[str, str | int]:
        return {
            "tool_prefix": self.tool_prefix,
        }

    def save_tool(self, tool: ToolProtocol) -> None:
        if not isinstance(tool, GenericTool):
            raise TypeError(f"expected GenericTool, got {type(tool).__name__}")
        logger.info(
            f"eddy_seek: staging tool {tool.tool_number} "
            f"offset=({tool.offset.x:.6f}, {tool.offset.y:.6f}) "
            f"calibrated={tool.is_calibrated}"
        )
        configfile = self._configfile
        configfile.remove_section(self.section_name(tool.tool_number))
        tool.save(configfile, self.tool_prefix)

    def apply_tool_offset(self, tool_number: int) -> GenericTool:
        tool = self._require_calibrated(tool_number)
        if not isinstance(tool, GenericTool):
            raise TypeError(f"expected GenericTool, got {type(tool).__name__}")
        eff = tool.effective_offset
        logger.info(
            f"eddy_seek: applying tool {tool_number} effective offset "
            f"({eff.x:.6f}, {eff.y:.6f})"
        )
        gcode = self._printer.lookup_object("gcode")
        gcode.run_script_from_command(f"SET_GCODE_OFFSET {eff.to_gcode()}")
        return tool
