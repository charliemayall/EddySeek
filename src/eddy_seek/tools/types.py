"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Kit type registry and ``tool_align_from_config`` factory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .bootstrap import detect_toolchanger_types as _detect_toolchanger_types
from .protocol import ToolAlignConfig
from .registry import DETECTION_ORDER, toolchanger_types

if TYPE_CHECKING:
    from klippy.configfile import ConfigWrapper


def tool_align_from_config(config: ConfigWrapper) -> ToolAlignConfig:
    """Build the kit ``ToolAlignConfig`` for ``toolchanger_type``."""
    types = toolchanger_types()
    toolchanger_type = config.get("toolchanger_type", "generic").strip().lower()
    try:
        impl = types[toolchanger_type]
    except KeyError as exc:
        known = ", ".join(sorted(types))
        raise config.error(
            f"eddy_seek: unknown toolchanger_type {toolchanger_type!r} (known: {known})"
        ) from exc
    return impl.from_config(config)


def detect_toolchanger_types(main_config: ConfigWrapper) -> list[str]:
    """Registered types whose ``suggest_for_config`` matches, in detection order."""
    return _detect_toolchanger_types(main_config, toolchanger_types(), DETECTION_ORDER)
