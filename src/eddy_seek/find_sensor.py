"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Walk-in seek loop for ``EDDY_SEEK_START FIND=1``.
"""

from __future__ import annotations

import logging

from .common import Offset
from .kconsole import KConsole
from .session import ArtifactRunContext, SeekHost, SeekSession, SeekSessionResult
from .strategy.base import SeekStrategy

logger = logging.getLogger(__name__)

_FIND_THRESHOLD_CAP_MM = 0.5
_FIND_THRESHOLD_TOLERANCE_FACTOR = 8
_DEFAULT_MAX_ITERS = 10


def find_sensor_threshold(tolerance: float) -> float:
    """Return stop threshold (mm): min(tolerance * 8, 0.5)."""
    return min(tolerance * _FIND_THRESHOLD_TOLERANCE_FACTOR, _FIND_THRESHOLD_CAP_MM)


def run_find_sensor(
    host: SeekHost,
    gcmd,
    *,
    console: KConsole,
    strategy: SeekStrategy,
    artifact: ArtifactRunContext,
    max_iters: int = _DEFAULT_MAX_ITERS,
) -> SeekSessionResult | None:
    """
    Repeat seeks from each finish position until offset magnitude is below threshold.

    Each successful seek leaves the nozzle at the found centre (SeekSession jog).
    """
    threshold = find_sensor_threshold(host.seek_config.tolerance)
    logger.info(
        f"eddy_seek: find sensor walk-in threshold={threshold:.4f} max_iters={max_iters}"
    )
    console.info(
        f"Walk-in target: offset < {threshold:.3g} mm (up to {max_iters} seeks)"
    )

    last_result: SeekSessionResult | None = None

    for iteration in range(1, max_iters + 1):
        logger.info(f"eddy_seek: find sensor seek {iteration}/{max_iters}")
        result = SeekSession(
            host,
            artifact=artifact,
            artifact_label=f"start_f{iteration}",
        ).run(gcmd, strategy, boundaries=False, recover_max_passes=True)

        if result.status != "ok" or result.offset is None:
            console.error(
                f"Find sensor failed on seek {iteration}"
                + (f": {result.error_message}" if result.error_message else "")
            )
            return None

        last_result = result
        magnitude = result.offset.distance_to(Offset.zero())
        logger.info(
            f"eddy_seek: find sensor seek {iteration} "
            f"offset=({result.offset.x:.4f}, {result.offset.y:.4f}) "
            f"magnitude={magnitude:.4f}"
        )
        console.info(
            f"Seek {iteration} - {result.offset.to_console_str()} ({magnitude:.3g} mm)"
        )

        if magnitude < threshold:
            seeks = "seek" if iteration == 1 else "seeks"
            console.exit(
                f"Found sensor centre - {result.offset.to_console_str()} "
                f"({iteration} {seeks})"
            )
            return result

    assert last_result is not None and last_result.offset is not None
    magnitude = last_result.offset.distance_to(Offset.zero())
    console.error(
        f"Find sensor did not get close enough after {max_iters} seeks "
        f"(offset {magnitude:.3g} mm, need < {threshold:.3g} mm). "
        "Reposition closer or widen max_jog."
    )
    return None
