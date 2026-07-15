"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Repeatability statistics for multi-run seeks.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass

from ..common import Offset
from ..kconsole import ConsoleSymbols, KConsole

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AccuracyStats:
    mean: Offset
    std_x: float
    std_y: float
    radial: tuple[float, ...]
    max_radial: float
    mean_radial: float
    max_pair: float
    xs_range: tuple[float, float]
    ys_range: tuple[float, float]


def _sample_stdev(values: Sequence[float], mean: float) -> float:
    if len(values) < 2:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def compute_accuracy_stats(offsets: Sequence[Offset]) -> AccuracyStats:
    n = len(offsets)
    xs = [p.x for p in offsets]
    ys = [p.y for p in offsets]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    std_x = _sample_stdev(xs, mean_x)
    std_y = _sample_stdev(ys, mean_y)

    mean = Offset(mean_x, mean_y)
    radial = tuple(offset.distance_to(mean) for offset in offsets)
    max_radial = max(radial)
    mean_radial = sum(radial) / n

    max_pair = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            max_pair = max(max_pair, offsets[i].distance_to(offsets[j]))

    return AccuracyStats(
        mean=mean,
        std_x=std_x,
        std_y=std_y,
        radial=radial,
        max_radial=max_radial,
        mean_radial=mean_radial,
        max_pair=max_pair,
        xs_range=(min(xs), max(xs)),
        ys_range=(min(ys), max(ys)),
    )


def report_accuracy_stats(
    console: KConsole,
    offsets: Sequence[Offset],
    *,
    durations_s: Sequence[float] | None = None,
) -> None:
    n = len(offsets)
    stats = compute_accuracy_stats(offsets)
    output = []
    for i, offset in enumerate(offsets, start=1):
        line = (
            f"#{i}  X={offset.x:+.2f} mm  Y={offset.y:+.2f} mm  "
            f"radial={stats.radial[i - 1]:.2f} mm"
        )
        if durations_s is not None and i <= len(durations_s):
            line += f"  t={durations_s[i - 1]:.1f}s"
        console.detail(line)
    output.extend(
        [
            f"Repeatability ({n} runs):",
            ConsoleSymbols.BR,
            f"mean X={stats.mean.x:+.2f} Y={stats.mean.y:+.2f} mm",
            ConsoleSymbols.BR,
            f"σ X={stats.std_x:.3f} Y={stats.std_y:.3f} mm",
            ConsoleSymbols.BR,
            ConsoleSymbols.BR,
            f"Max scatter: {stats.max_radial:.3f} mm",
            ConsoleSymbols.BR,
            f"Max pairwise {stats.max_pair:.3f} mm",
            ConsoleSymbols.BR,
        ]
    )

    if durations_s:
        mean_t = sum(durations_s) / len(durations_s)
        output.extend(
            [
                ConsoleSymbols.BR,
                f"Seek time ({len(durations_s)} runs): ",
                ConsoleSymbols.BR,
                f"mean {mean_t:.1f}s ",
                ConsoleSymbols.BR,
                f"(min {min(durations_s):.1f}s, max {max(durations_s):.1f}s)",
            ]
        )
    console.info("".join(output))
    logger.info(
        f"eddy_seek: accuracy report n={n} mean=({stats.mean.x:.4f}, {stats.mean.y:.4f}) "
        f"stdev=({stats.std_x:.4f}, {stats.std_y:.4f}) "
        f"max_radial={stats.max_radial:.4f} max_pair={stats.max_pair:.4f}"
        + (
            f" seek_time_mean={sum(durations_s) / len(durations_s):.2f}s"
            if durations_s
            else ""
        )
    )
