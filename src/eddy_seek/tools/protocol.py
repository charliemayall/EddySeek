"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

``ToolProtocol`` and ``ToolAlignConfig`` ABC shared by all toolchanger kits.
"""

from __future__ import annotations

import dataclasses
import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ..common import Offset

if TYPE_CHECKING:
    from klippy.configfile import ConfigWrapper, PrinterConfig
    from klippy.klippy import Printer


logger = logging.getLogger(__name__)


class ToolProtocol(Protocol):
    """Runtime shape shared by alignment callers. Kit types must implement this."""

    tool_number: int
    offset: Offset
    is_calibrated: bool

    @property
    def effective_offset(self) -> Offset: ...

    def mark_calibrated(self, offset: Offset | None = None) -> ToolProtocol: ...

    def to_dict(self) -> dict[str, float | int | bool]: ...


def calibrated_offset(offset: Offset | None) -> Offset:
    return offset if offset is not None else Offset.zero()


@dataclass
class ToolRecord:
    """Shared tool fields and helpers. Kit types subclass and satisfy ``ToolProtocol``."""

    tool_number: int
    offset: Offset
    is_calibrated: bool

    def to_dict(self) -> dict[str, float | int | bool]:
        return dataclasses.asdict(self)


class ToolAlignConfig(ABC):
    """Kit-agnostic tool alignment config. Subclasses own kit keys and persistence."""

    def __init__(
        self,
        *,
        printer: Printer,
        tool_count: int,
        tools: Sequence[ToolProtocol],
        sensor_z: float | None,
        toolchanger_type: str,
    ) -> None:
        self._printer = printer
        self.tool_count = tool_count
        self.tools: list[ToolProtocol] = list(tools)
        self.sensor_z = sensor_z
        self.toolchanger_type = toolchanger_type

    @classmethod
    def from_config(cls, config: ConfigWrapper) -> ToolAlignConfig:
        """Build this kit. For dispatch by ``toolchanger_type``, use ``tool_align_from_config``."""
        if cls is ToolAlignConfig:
            raise TypeError(
                "ToolAlignConfig.from_config is kit-specific"
                "use tools.tool_align_from_config"
            )
        return cls._from_config(config)

    @classmethod
    @abstractmethod
    def _from_config(cls, config: ConfigWrapper) -> ToolAlignConfig:
        """Build this kit from the ``[eddy_seek]`` section."""

    @classmethod
    def suggest_for_config(cls, main_config: ConfigWrapper) -> bool:
        """Return True when printer config fingerprints match this kit."""
        return False

    @classmethod
    def suggestion_reason(cls, main_config: ConfigWrapper) -> str | None:
        """Human-readable reason for a suggestion, when applicable."""
        return None

    @abstractmethod
    def save_tool(self, tool: ToolProtocol) -> None:
        """Persist one tool's offsets to kit-specific storage."""

    def supports_apply_offset(self) -> bool:
        """Whether ``EDDY_SEEK_APPLY_OFFSET`` is meaningful for this kit."""
        return True

    def apply_tool_offset(self, tool_number: int) -> ToolProtocol:
        """Apply a calibrated tool's stored XY offset."""
        raise ValueError(
            "EDDY_SEEK_APPLY_OFFSET is not supported for this toolchanger kit"
        )

    @abstractmethod
    def tool_status_key(self, tool_number: int) -> str:
        """Key used in ``get_status`` / Moonraker for this tool."""

    def status_tools(self) -> dict[str, dict[str, float | int | bool]]:
        return {
            self.tool_status_key(tool.tool_number): tool.to_dict()
            for tool in self.tools
        }

    def kit_trace(self) -> dict[str, str | int]:
        """Optional kit-specific fields for session traces."""
        return {}

    def persist_hint(self) -> str:
        """Console hint after a successful batch align."""
        return "run SAVE_CONFIG to persist"

    def get_tool(self, tool_number: int) -> ToolProtocol:
        if tool_number < 0 or tool_number >= self.tool_count:
            raise IndexError(
                f"tool {tool_number} out of range 0..{self.tool_count - 1}"
            )
        return self.tools[tool_number]

    def update_tool(self, tool: ToolProtocol) -> None:
        self.tools[tool.tool_number] = tool

    @property
    def _configfile(self) -> PrinterConfig:
        return self._printer.lookup_object("configfile")

    def _require_calibrated(self, tool_number: int) -> ToolProtocol:
        try:
            tool = self.get_tool(tool_number)
        except IndexError as exc:
            raise ValueError(str(exc)) from exc
        if not tool.is_calibrated:
            raise ValueError(
                f"Tool {tool_number} is not calibrated, and you are trying to apply an offset."
            )
        return tool

    @staticmethod
    def parse_sensor_z_config(config: ConfigWrapper) -> float | None:
        sensor_z_raw = config.get("sensor_z", None)
        if sensor_z_raw is None:
            return None
        return float(config.getfloat("sensor_z"))
