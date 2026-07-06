"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.

Repeatability test orchestration for EDDY_SEEK_ACCURACY.
"""

from __future__ import annotations

import logging
import random
import uuid
from datetime import datetime
from pathlib import Path

from .common import Offset, Position
from .config import SeekConfig
from .kconsole import KConsole
from .movement.handler import manual_move_xy
from .plotting.accuracy import write_accuracy_plot
from .plotting.artifacts import write_figure
from .plotting.primitives import AccuracyRepeatRecord
from .plotting.recorder import SessionRecorder
from .session import SeekHost, SeekSession, report_accuracy_stats
from .strategy import strategy_for

logger = logging.getLogger(__name__)

_GCODE_STATE = "EDDY_SEEK_ACCURACY"


def _apply_mock_offset(host: SeekHost, cfg: SeekConfig, console: KConsole) -> Offset:
    mock_span = min(cfg.max_jog_x, cfg.max_jog_y, random.random() * 0.6)
    mock_offset = Offset(
        (random.random() * 2 - 1) * mock_span,
        (random.random() * 2 - 1) * mock_span,
    )
    console.info(f"Mock offset: {mock_offset.to_delta_str()}")
    toolhead = host.printer.lookup_object("toolhead")
    machine = Position.from_pair(toolhead.get_position()) + mock_offset
    manual_move_xy(toolhead, machine, cfg.jog_speed / 60.0)
    toolhead.wait_moves()
    return mock_offset


def _write_accuracy_plot(
    host: SeekHost,
    *,
    records: tuple[AccuracyRepeatRecord, ...],
    run_id: str,
    write_at: datetime,
) -> str | None:
    cfg = host.seek_config
    fig = write_accuracy_plot(repeats=list(records))
    if fig is None:
        return None
    return write_figure(
        Path(cfg.result_folder),
        fig,
        write_at=write_at,
        suffix="accuracy",
        run_label="accuracy",
        run_id=run_id,
    )


def run_accuracy_test(
    host: SeekHost,
    gcmd,
    *,
    console: KConsole,
    repeats: int,
    mock_enabled: bool,
) -> None:
    gcode = host.printer.lookup_object("gcode")
    gcode.run_script_from_command(f"SAVE_GCODE_STATE NAME={_GCODE_STATE}")

    cfg = host.seek_config
    run_id = uuid.uuid4().hex[:8]
    write_at = datetime.now()
    recorder = SessionRecorder(trace=False, plots=cfg.save_plots)

    offsets: list[Offset] = []
    durations_s: list[float] = []
    try:
        for repeat in range(1, repeats + 1):
            if repeat > 1:
                gcode.run_script_from_command(
                    f"RESTORE_GCODE_STATE NAME={_GCODE_STATE} MOVE=1"
                )

            mock_offset = Offset.zero()
            if mock_enabled:
                mock_offset = _apply_mock_offset(host, cfg, console)

            console.info(f"Repeat {repeat}/{repeats}")
            result = SeekSession(
                host,
                run_id=run_id,
                run_label="accuracy",
                artifact_label=f"r{repeat}",
                artifact_write_at=write_at,
            ).run(
                gcmd,
                strategy_for(cfg.strategy),
                boundaries=False,
                announce_plot=True,
            )

            if result.status != "ok" or result.offset is None:
                console.error(
                    f"Repeat {repeat} failed"
                    + (f": {result.error_message}" if result.error_message else "")
                )
                break

            found_offset = mock_offset + result.offset
            offsets.append(found_offset)
            durations_s.append(result.end_time - result.start_time)
            recorder.record(
                AccuracyRepeatRecord(
                    repeat_num=repeat,
                    offset=found_offset,
                    session_plot_path=result.plot_path,
                )
            )
            console.info(
                f"Repeat {repeat} - X={found_offset.x:+.4f} "
                f"Y={found_offset.y:+.4f} mm "
                f"({durations_s[-1]:.1f}s)"
            )

        if len(offsets) < 2:
            console.error("Need at least 2 successful repeats for deviation report")
            return

        report_accuracy_stats(console, offsets, durations_s=durations_s)
        if cfg.save_plots and len(offsets) >= 2:
            plot_path = _write_accuracy_plot(
                host,
                records=recorder.records(),
                run_id=run_id,
                write_at=write_at,
            )
            if plot_path is not None:
                console.plot_saved(plot_path)
                logger.info(f"eddy_seek: accuracy plot saved to {plot_path}")
        console.exit(f"Accuracy test complete ({len(offsets)} repeats)")
    finally:
        gcode.run_script_from_command(f"RESTORE_GCODE_STATE NAME={_GCODE_STATE} MOVE=1")
