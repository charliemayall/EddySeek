"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Bondtech INDX toolchanger integration.

Upstream macros and config (do not fork these names without checking Bondtech):
https://github.com/BondtechAB/INDX/tree/main/macros

EddySeek relies on:

- **Load macro** - ``CHANGE_TOOL TOOL={n}`` from ``indx-tc-macros.cfg`` (``format_load_macro``).
- **Kit detection** - printer config contains ``[indx]``
  (Bondtech INDX Klipper object; see upstream README).
- **Tool count** - ``[gcode_macro TOOL_POSITIONS]`` option ``tool_count`` or
  ``variable_tool_count`` (see ``_indx_tool_count``).
- **XY persistence** - Klipper ``SAVE_VARIABLE`` keys ``t{n}_offset_x`` and
  ``t{n}_offset_y``. Bondtech ``_PICKUP_TOOL`` reads these when applying
  ``SET_GCODE_OFFSET`` at print time.

In INDX mode, EddySeek does **not** use:
- ``[es_Tn]`` sections (DIY only).
- ``manual_adjust_x/y`` (DIY only).
- ``EDDY_SEEK_APPLY_OFFSET`` - ``CHANGE_TOOL`` already applies XY from save vars.
- Z offsets - use Bondtech ``CAL_Z`` / ``t{n}_offset_z`` (``indx-cal.cfg``), not EddySeek.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..common import Offset
from .bootstrap import log_kit_startup, read_config_context, run_detection
from .protocol import ToolAlignConfig, ToolProtocol, ToolRecord, calibrated_offset
from .registry import DETECTION_ORDER, toolchanger_types

if TYPE_CHECKING:
    from klippy.configfile import ConfigWrapper
    from klippy.klippy import Printer

logger = logging.getLogger(__name__)

_TOOL_POSITIONS_SECTION = "gcode_macro TOOL_POSITIONS"
_INDX_SECTION = "indx"
_DIY_ONLY_KEYS = frozenset({"tool_count", "load_tool_macro_prefix", "tool_prefix"})


@dataclass
class IndxTool(ToolRecord):
    @property
    def effective_offset(self) -> Offset:
        return self.offset

    @classmethod
    def create_default(cls, tool_number: int) -> IndxTool:
        return cls(
            tool_number=tool_number,
            offset=Offset.zero(),
            is_calibrated=False,
        )

    def mark_calibrated(self, offset: Offset | None = None) -> IndxTool:
        """Return a copy marked calibrated with the given seek offset."""
        return dataclasses.replace(
            self,
            offset=calibrated_offset(offset),
            is_calibrated=True,
        )


def _has_indx_kit(main_config: ConfigWrapper) -> bool:
    return main_config.has_section(_INDX_SECTION)


def _indx_tool_count(main_config: ConfigWrapper) -> int:
    if not main_config.has_section(_TOOL_POSITIONS_SECTION):
        raise ValueError(
            "eddy_seek: toolchanger_type=indx requires "
            f"[{_TOOL_POSITIONS_SECTION}] in printer config"
        )
    section = main_config.getsection(_TOOL_POSITIONS_SECTION)
    for key in ("variable_tool_count", "tool_count"):
        if section.get(key, None) is not None:
            return section.getint(key, minval=1)
    raise ValueError(
        f"eddy_seek: [{_TOOL_POSITIONS_SECTION}] missing tool_count "
        "(expected variable_tool_count or tool_count)"
    )


def _save_variable_dict(printer: Printer) -> dict[str, Any]:
    """Return ``save_variables.allVariables``, or ``{}`` if the module is absent."""
    try:
        sv = printer.lookup_object("save_variables")
    except Exception:
        return {}
    vars_ = getattr(sv, "allVariables", None)
    return dict(vars_) if vars_ else {}


class IndxToolAlignConfig(ToolAlignConfig):
    """Bondtech INDX: ``CHANGE_TOOL`` load, ``SAVE_VARIABLE`` offset persistence."""

    def __init__(
        self,
        *,
        printer,
        tool_count: int,
        tools: list[IndxTool],
        sensor_x: float,
        sensor_y: float,
        sensor_z: float | None,
    ) -> None:
        super().__init__(
            printer=printer,
            tool_count=tool_count,
            tools=tools,
            sensor_x=sensor_x,
            sensor_y=sensor_y,
            sensor_z=sensor_z,
            toolchanger_type="indx",
        )

    @classmethod
    def _from_config(cls, config: ConfigWrapper) -> IndxToolAlignConfig:
        option_names = set(config.get_prefix_options(""))
        for key in _DIY_ONLY_KEYS:
            if key in option_names:
                raise config.error(
                    f"eddy_seek: toolchanger_type=indx does not use {key} "
                    f"(remove it from [eddy_seek])"
                )

        sensor_pos, printer, main_config = read_config_context(config)
        try:
            tool_count = _indx_tool_count(main_config)
        except ValueError as exc:
            raise config.error(str(exc)) from exc

        types = toolchanger_types()
        run_detection("indx", main_config, types, DETECTION_ORDER)

        saved = _save_variable_dict(printer)
        tools = [cls._load_tool_or_default(saved, n) for n in range(tool_count)]
        log_kit_startup(
            toolchanger_type="indx",
            tool_count=tool_count,
            sensor_pos=sensor_pos,
        )
        return cls(
            printer=printer,
            tool_count=tool_count,
            tools=tools,
            sensor_x=sensor_pos.x,
            sensor_y=sensor_pos.y,
            sensor_z=sensor_pos.z,
        )

    @staticmethod
    def _load_tool_or_default(
        saved_variables: dict[str, Any], tool_number: int
    ) -> IndxTool:
        x_key = f"t{tool_number}_offset_x"
        y_key = f"t{tool_number}_offset_y"
        if x_key not in saved_variables and y_key not in saved_variables:
            return IndxTool.create_default(tool_number)
        return IndxTool(
            tool_number=tool_number,
            offset=Offset(
                x=float(saved_variables.get(x_key, 0.0)),
                y=float(saved_variables.get(y_key, 0.0)),
            ),
            is_calibrated=True,
        )

    @classmethod
    def suggest_for_config(cls, main_config: ConfigWrapper) -> bool:
        return _has_indx_kit(main_config)

    @classmethod
    def suggestion_reason(cls, main_config: ConfigWrapper) -> str | None:
        if _has_indx_kit(main_config):
            return f"[{_INDX_SECTION}] section in config"
        return None

    def format_load_macro(self, tool_number: int) -> str:
        return f"CHANGE_TOOL TOOL={tool_number}"

    def tool_status_key(self, tool_number: int) -> str:
        return f"t{tool_number}"

    def persist_hint(self) -> str:
        return "XY offsets written to save_variables"

    def supports_apply_offset(self) -> bool:
        return False

    def save_tool(self, tool: ToolProtocol) -> None:
        if not isinstance(tool, IndxTool):
            raise TypeError(f"expected IndxTool, got {type(tool).__name__}")
        logger.info(
            f"eddy_seek: saving tool {tool.tool_number} via SAVE_VARIABLE "
            f"offset=({tool.offset.x:.6f}, {tool.offset.y:.6f}) "
            f"calibrated={tool.is_calibrated}"
        )
        self._save_variables(tool.tool_number, tool.offset)

    def _save_variables(self, tool_number: int, offset: Offset) -> None:
        gcode = self._printer.lookup_object("gcode")
        gcode.run_script_from_command(
            f"SAVE_VARIABLE VARIABLE=t{tool_number}_offset_x VALUE={offset.x:.6f}"
        )
        gcode.run_script_from_command(
            f"SAVE_VARIABLE VARIABLE=t{tool_number}_offset_y VALUE={offset.y:.6f}"
        )
