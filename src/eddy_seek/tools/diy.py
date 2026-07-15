"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

import dataclasses
import logging
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


@dataclass
class DiyTool(ToolRecord):
    manual_offset: Offset

    @property
    def effective_offset(self) -> Offset:
        return self.offset + self.manual_offset

    @classmethod
    def create_default(cls, tool_number: int) -> DiyTool:
        return cls(
            tool_number=tool_number,
            offset=Offset.zero(),
            manual_offset=Offset.zero(),
            is_calibrated=False,
        )

    @staticmethod
    def load(config: ConfigWrapper, prefix: str, tool_number: int) -> DiyTool:
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
        return DiyTool(
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

    def mark_calibrated(self, offset: Offset | None = None) -> DiyTool:
        """Return a copy marked calibrated with the given seek offset."""
        return dataclasses.replace(
            self,
            offset=calibrated_offset(offset),
            is_calibrated=True,
        )


class DiyToolAlignConfig(ToolAlignConfig):
    """Generic load-macro toolchanger (``T0``, ``T1``, …) with ``[es_Tn]`` persistence."""

    def __init__(
        self,
        *,
        printer,
        tool_count: int,
        tools: list[DiyTool],
        sensor_x: float,
        sensor_y: float,
        sensor_z: float | None,
        tool_prefix: str,
        load_tool_macro: str,
    ) -> None:
        super().__init__(
            printer=printer,
            tool_count=tool_count,
            tools=tools,
            sensor_x=sensor_x,
            sensor_y=sensor_y,
            sensor_z=sensor_z,
            toolchanger_type="diy",
        )
        self.tool_prefix = tool_prefix
        self.load_tool_macro = load_tool_macro

    @classmethod
    def _from_config(cls, config: ConfigWrapper) -> DiyToolAlignConfig:
        tool_prefix = config.get("tool_prefix", "es_T")
        load_tool_macro = config.get("load_tool_macro_prefix", "T")
        sensor_pos, printer, main_config = read_config_context(config)
        tool_count = config.getint("tool_count", 1, minval=1)
        types = toolchanger_types()
        run_detection("diy", main_config, types, DETECTION_ORDER)

        tools = [
            cls._load_tool_or_default(main_config, tool_prefix, n)
            for n in range(tool_count)
        ]
        log_kit_startup(
            toolchanger_type="diy",
            tool_count=tool_count,
            sensor_pos=sensor_pos,
            extra=f"prefix={tool_prefix!r} load_macro={load_tool_macro!r}",
        )
        return cls(
            printer=printer,
            tool_count=tool_count,
            tools=tools,
            sensor_x=sensor_pos.x,
            sensor_y=sensor_pos.y,
            sensor_z=sensor_pos.z,
            tool_prefix=tool_prefix,
            load_tool_macro=load_tool_macro,
        )

    @staticmethod
    def _load_tool_or_default(
        main_config: ConfigWrapper, tool_prefix: str, tool_number: int
    ) -> DiyTool:
        section = f"{tool_prefix}{tool_number}"
        if not main_config.has_section(section):
            return DiyTool.create_default(tool_number)
        return DiyTool.load(main_config, tool_prefix, tool_number)

    def section_name(self, tool_number: int) -> str:
        return f"{self.tool_prefix}{tool_number}"

    def format_load_macro(self, tool_number: int) -> str:
        return f"{self.load_tool_macro}{tool_number}"

    def tool_status_key(self, tool_number: int) -> str:
        return self.section_name(tool_number)

    def kit_trace(self) -> dict[str, str | int]:
        return {
            "tool_prefix": self.tool_prefix,
            "load_tool_macro_prefix": self.load_tool_macro,
        }

    def save_tool(self, tool: ToolProtocol) -> None:
        if not isinstance(tool, DiyTool):
            raise TypeError(f"expected DiyTool, got {type(tool).__name__}")
        logger.info(
            f"eddy_seek: staging tool {tool.tool_number} "
            f"offset=({tool.offset.x:.6f}, {tool.offset.y:.6f}) "
            f"calibrated={tool.is_calibrated}"
        )
        configfile = self._configfile
        configfile.remove_section(self.section_name(tool.tool_number))
        tool.save(configfile, self.tool_prefix)

    def apply_tool_offset(self, tool_number: int) -> DiyTool:
        tool = self._require_calibrated(tool_number)
        if not isinstance(tool, DiyTool):
            raise TypeError(f"expected DiyTool, got {type(tool).__name__}")
        eff = tool.effective_offset
        logger.info(
            f"eddy_seek: applying tool {tool_number} effective offset "
            f"({eff.x:.6f}, {eff.y:.6f})"
        )
        gcode = self._printer.lookup_object("gcode")
        gcode.run_script_from_command(f"SET_GCODE_OFFSET {eff.to_gcode()}")
        return tool
