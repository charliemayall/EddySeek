"""
EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.

*Copyright (C) 2026 Charlie Mayall*

This file may be distributed under the terms of the GNU GPLv3 license.
"""

from __future__ import annotations

from unittest.mock import patch

from fakes import FakeGcmd, FakePrinter, RecordingToolhead, ok_seek_result

from eddy_seek.common import Offset, Position
from eddy_seek.config import SeekConfig
from eddy_seek.kconsole import KConsole
from eddy_seek.repeated_seek import run_repeated_seeks


def _console() -> KConsole:
    return KConsole(FakeGcmd(), SeekConfig())


class _FakeHost:
    def __init__(self, printer: FakePrinter) -> None:
        self.printer = printer
        self.seek_config = SeekConfig()


def test_run_repeated_seeks_physically_returns_between_repeats():
    """Repeat 2+ must jog back even when RESTORE sees matching gcode coords."""
    start = Position(150.0, 150.0)
    toolhead = RecordingToolhead(start=(start.x, start.y))
    printer = FakePrinter(toolhead=toolhead)
    host = _FakeHost(printer)
    console = _console()
    move_targets: list[Position] = []

    def run_once(_repeat: int):
        toolhead.pos[0] = 150.003
        toolhead.pos[1] = 150.001
        return ok_seek_result(offset=Offset(0.003, 0.001))

    def capture_move(_toolhead, pos, _speed, *, wait=True):
        move_targets.append(pos)

    with patch(
        "eddy_seek.repeated_seek.move_to_xy",
        side_effect=capture_move,
    ):
        result = run_repeated_seeks(
            host,  # ty: ignore[invalid-argument-type]
            console=console,
            repeats=3,
            gcode_state_name="_test_repeat",
            run_once=run_once,
        )

    assert result is not None
    assert len(result.offsets) == 3
    assert move_targets == [start, start]
