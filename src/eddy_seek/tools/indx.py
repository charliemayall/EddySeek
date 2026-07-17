"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Bondtech INDX toolchanger integration.

Upstream macros and config (do not fork these names without checking Bondtech):
https://github.com/BondtechAB/INDX/tree/main/macros

EddySeek relies on:

- **Kit detection** - printer config contains ``[indx]``
  (Bondtech INDX Klipper object; see upstream README).
- **Tool count** - ``[gcode_macro TOOL_POSITIONS]`` option ``variable_tool_count``
  (see ``_indx_tool_count``).
- **XY persistence** - Klipper ``SAVE_VARIABLE`` keys ``t{n}_offset_x`` and
  ``t{n}_offset_y``. Bondtech ``_PICKUP_TOOL`` reads these when applying
  ``SET_GCODE_OFFSET`` at print time.

In INDX mode, EddySeek does **not** use:
- ``[es_Tn]`` sections (Generic only).
- ``manual_adjust_x/y`` (Generic only).
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
_INDX_TOOL_COUNT_KEYS = ("variable_tool_count", "tool_count")
_GENERIC_ONLY_KEYS = frozenset({"tool_prefix"})


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
    for key in _INDX_TOOL_COUNT_KEYS:
        if section.get(key, None) is not None:
            return section.getint(key, minval=1)
    raise ValueError(
        f"eddy_seek: [{_TOOL_POSITIONS_SECTION}] missing tool_count "
        "(expected variable_tool_count or tool_count)"
    )


def _save_variable_dict(printer: Printer) -> dict[str, Any]:
    """Return ``save_variables.allVariables`` (INDX requires the module)."""
    try:
        sv = printer.lookup_object("save_variables")
    except Exception as exc:
        raise ValueError(
            "eddy_seek: toolchanger_type=indx requires [save_variables] in printer config"
        ) from exc
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
        sensor_z: float | None,
    ) -> None:
        super().__init__(
            printer=printer,
            tool_count=tool_count,
            tools=tools,
            sensor_z=sensor_z,
            toolchanger_type="indx",
        )

    @classmethod
    def _from_config(cls, config: ConfigWrapper) -> IndxToolAlignConfig:
        option_names = set(config.get_prefix_options(""))
        for key in _GENERIC_ONLY_KEYS:
            if key in option_names:
                raise config.error(
                    f"eddy_seek: toolchanger_type=indx does not use {key} "
                    f"(remove it from [eddy_seek])"
                )

        sensor_z, printer, main_config = read_config_context(config)
        try:
            tool_count = _indx_tool_count(main_config)
        except ValueError as exc:
            raise config.error(str(exc)) from exc

        types = toolchanger_types()
        run_detection("indx", main_config, types, DETECTION_ORDER)

        saved = _save_variable_dict(printer)
        try:
            tools = [cls._load_tool_or_default(saved, n) for n in range(tool_count)]
        except ValueError as exc:
            raise config.error(str(exc)) from exc
        log_kit_startup(
            toolchanger_type="indx",
            tool_count=tool_count,
            sensor_z=sensor_z,
        )
        return cls(
            printer=printer,
            tool_count=tool_count,
            tools=tools,
            sensor_z=sensor_z,
        )

    @staticmethod
    def _load_tool_or_default(
        saved_variables: dict[str, Any], tool_number: int
    ) -> IndxTool:
        x_key = f"t{tool_number}_offset_x"
        y_key = f"t{tool_number}_offset_y"
        has_x = x_key in saved_variables
        has_y = y_key in saved_variables
        if not has_x and not has_y:
            # missing keys = uncalibrated, not a bad macro slot - slots come
            # from the same TOOL_POSITIONS tool_count; Bondtech shows "--" until CAL.
            return IndxTool.create_default(tool_number)
        if has_x != has_y:
            present = x_key if has_x else y_key
            missing = y_key if has_x else x_key
            raise ValueError(
                f"eddy_seek: incomplete INDX offsets for tool {tool_number} - "
                f"found {present} but not {missing}; "
                f"fix save_variables so both {x_key} and {y_key} are present"
            )
        return IndxTool(
            tool_number=tool_number,
            offset=Offset(
                x=float(saved_variables[x_key]),
                y=float(saved_variables[y_key]),
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
