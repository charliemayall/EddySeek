"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Shared kit init ceremony (sensor parse, detection logging, startup log).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .protocol import ToolAlignConfig

if TYPE_CHECKING:
    from klippy.configfile import ConfigWrapper
    from klippy.klippy import Printer

logger = logging.getLogger(__name__)


def read_config_context(
    config: ConfigWrapper,
) -> tuple[float | None, Printer, ConfigWrapper]:
    sensor_z = ToolAlignConfig.parse_sensor_z_config(config)
    printer = config.get_printer()
    main_config = printer.lookup_object("configfile").read_main_config()
    return sensor_z, printer, main_config


def detect_toolchanger_types(
    main_config: ConfigWrapper,
    registry: dict[str, type[ToolAlignConfig]],
    detection_order: tuple[str, ...],
) -> list[str]:
    return [
        name
        for name in detection_order
        if registry[name].suggest_for_config(main_config)
    ]


def log_toolchanger_suggestion(
    active_name: str,
    main_config: ConfigWrapper,
    detected: list[str],
    registry: dict[str, type[ToolAlignConfig]],
) -> None:
    for name in detected:
        if name == active_name:
            continue
        cls = registry[name]
        reason = cls.suggestion_reason(main_config)
        detail = f" (found {reason})" if reason else ""
        logger.info(
            f"eddy_seek: printer config suggests toolchanger_type: {name}{detail}"
        )


def log_kit_startup(
    *,
    toolchanger_type: str,
    tool_count: int,
    sensor_z: float | None,
    extra: str = "",
) -> None:
    sensor_z_text = f"{sensor_z:.4f}" if sensor_z is not None else "unset"
    suffix = f" {extra}" if extra else ""
    logger.info(
        f"eddy_seek: tools config toolchanger_type={toolchanger_type} "
        f"tool_count={tool_count} sensor_z={sensor_z_text}{suffix}"
    )


def run_detection(
    active_name: str,
    main_config: ConfigWrapper,
    registry: dict[str, type[ToolAlignConfig]],
    detection_order: tuple[str, ...],
) -> None:
    detected = detect_toolchanger_types(main_config, registry, detection_order)
    log_toolchanger_suggestion(active_name, main_config, detected, registry)
