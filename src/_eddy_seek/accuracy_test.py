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
from dataclasses import replace
from datetime import datetime

from .common import Offset, Position
from .config import SeekConfig
from .kconsole import KConsole
from .movement.handler import manual_move_xy
from .repeated_seek import run_repeated_seeks, write_repeat_scatter_plot
from .session import SeekHost, SeekSession, SeekSessionResult, report_accuracy_stats
from .strategy import strategy_for

logger = logging.getLogger(__name__)

_GCODE_STATE = "EDDY_SEEK_ACCURACY"


def _apply_mock_offset(host: SeekHost, cfg: SeekConfig, console: KConsole) -> Offset:
    mock_span = min(cfg.max_jog_x, cfg.max_jog_y, random.random() * 0.6)
    mock_offset = Offset(
        (random.random() * 2 - 1) * mock_span,
        (random.random() * 2 - 1) * mock_span,
    )
    console.info(f"Mock offset: {mock_offset.to_console_str()}")
    toolhead = host.printer.lookup_object("toolhead")
    machine = Position.from_pair(toolhead.get_position()) + mock_offset
    manual_move_xy(toolhead, machine, cfg.jog_speed / 60.0)
    toolhead.wait_moves()
    return mock_offset


def run_accuracy_test(
    host: SeekHost,
    gcmd,
    *,
    console: KConsole,
    repeats: int,
    mock_enabled: bool,
) -> None:
    cfg = host.seek_config
    run_id = uuid.uuid4().hex[:8]
    write_at = datetime.now()

    def run_once(repeat: int) -> SeekSessionResult:
        mock_offset = Offset.zero()
        if mock_enabled:
            mock_offset = _apply_mock_offset(host, cfg, console)

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
            return result
        return replace(result, offset=mock_offset + result.offset)

    repeated = run_repeated_seeks(
        host,
        console=console,
        repeats=repeats,
        gcode_state_name=_GCODE_STATE,
        run_once=run_once,
    )

    if repeated is None:
        return

    offsets = list(repeated.offsets)
    durations_s = list(repeated.durations_s)
    if len(offsets) < 2:
        console.error("Need at least 2 successful repeats for deviation report")
        return

    report_accuracy_stats(console, offsets, durations_s=durations_s)
    if cfg.save_plots and len(offsets) >= 2:
        plot_path = write_repeat_scatter_plot(
            host,
            records=repeated.records,
            run_id=run_id,
            write_at=write_at,
            suffix="accuracy",
            run_label="accuracy",
        )
        if plot_path is not None:
            console.plot_saved(plot_path)
            logger.info(f"eddy_seek: accuracy plot saved to {plot_path}")
    console.exit(f"Accuracy test complete ({len(offsets)} repeats)")
