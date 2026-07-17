"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Toolchanger kit registry without importing kit modules at load time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .protocol import ToolAlignConfig

DETECTION_ORDER: tuple[str, ...] = ("indx",)

_TYPES: dict[str, type[ToolAlignConfig]] | None = None


def toolchanger_types() -> dict[str, type[ToolAlignConfig]]:
    global _TYPES
    if _TYPES is None:
        from .generic import GenericToolAlignConfig
        from .indx import IndxToolAlignConfig

        _TYPES = {
            "generic": GenericToolAlignConfig,
            "indx": IndxToolAlignConfig,
        }
    return _TYPES
