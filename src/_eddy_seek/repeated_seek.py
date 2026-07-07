"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Shared repeat-and-average seek loop for EDDY_SEEK_ACCURACY and tool alignment.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .common import Offset, Position
from .kconsole import KConsole
from .movement.gcode_state import GCodeState
from .movement.handler import move_to_xy
from .plotting.accuracy import write_accuracy_plot
from .plotting.artifacts import write_figure
from .plotting.primitives import AccuracyRepeatRecord
from .session import SeekHost, SeekSessionResult, compute_accuracy_stats


@dataclass(frozen=True, slots=True)
class RepeatedSeekResult:
    offsets: tuple[Offset, ...]
    durations_s: tuple[float, ...]
    records: tuple[AccuracyRepeatRecord, ...]
    mean: Offset
    last_result: SeekSessionResult | None = None


def write_repeat_scatter_plot(
    host: SeekHost,
    *,
    records: tuple[AccuracyRepeatRecord, ...],
    run_id: str,
    write_at: datetime,
    suffix: str,
    run_label: str,
) -> str | None:
    cfg = host.seek_config
    fig = write_accuracy_plot(repeats=list(records))
    if fig is None:
        return None
    return write_figure(
        Path(cfg.result_folder),
        fig,
        write_at=write_at,
        suffix=suffix,
        run_label=run_label,
    )


def run_repeated_seeks(
    host: SeekHost,
    *,
    console: KConsole,
    repeats: int,
    gcode_state_name: str,
    run_once: Callable[[int], SeekSessionResult],
) -> RepeatedSeekResult | None:
    """
    Run the same seek ``repeats`` times from a saved G-code position.

    ``run_once`` must run a single seek; position before the first repeat when
    ``repeats >= 2``.  When ``repeats == 1``, no inner SAVE/RESTORE is used.
    """
    if repeats < 1:
        return None

    gcode = host.printer.lookup_object("gcode")
    offsets: list[Offset] = []
    durations_s: list[float] = []
    records: list[AccuracyRepeatRecord] = []
    last_result: SeekSessionResult | None = None

    if repeats == 1:
        result = run_once(1)
        if result.status != "ok" or result.offset is None:
            if result.error_message:
                console.error(result.error_message)
            return None
        return RepeatedSeekResult(
            offsets=(result.offset,),
            durations_s=(result.end_time - result.start_time,),
            records=(),
            mean=result.offset,
            last_result=result,
        )

    start_pos = Position.from_toolhead(host.printer)
    with GCodeState(gcode, gcode_state_name, move_on_restore=True):
        for repeat in range(1, repeats + 1):
            if repeat > 1:
                move_to_xy(
                    host.printer.lookup_object("toolhead"),
                    start_pos,
                    host.seek_config.jog_speed,
                    wait=True,
                )

            console.info(f"Repeat {repeat}/{repeats}")
            result = run_once(repeat)
            if result.status != "ok" or result.offset is None:
                console.error(
                    f"Repeat {repeat} failed"
                    + (f": {result.error_message}" if result.error_message else "")
                )
                return None

            offset = result.offset
            duration = result.end_time - result.start_time
            offsets.append(offset)
            durations_s.append(duration)
            records.append(
                AccuracyRepeatRecord(
                    repeat_num=repeat,
                    offset=offset,
                    session_plot_path=result.plot_path,
                )
            )
            last_result = result
            console.info(
                f"Repeat {repeat} - {offset.to_console_str()} ({duration:.1f}s)"
            )

        mean = compute_accuracy_stats(offsets).mean
        return RepeatedSeekResult(
            offsets=tuple(offsets),
            durations_s=tuple(durations_s),
            records=tuple(records),
            mean=mean,
            last_result=last_result,
        )
