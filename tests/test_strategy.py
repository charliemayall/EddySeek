"""
# EddySeek - Eddy sensor nozzle alignment on toolchanger and nozzle change 3D printers running Klipper firmware.
#
# Copyright (C) 2026 Charlie Mayall
#
# This file may be distributed under the terms of the GNU GPLv3 license.
"""

import math

from pytest import raises

from _eddy_seek.common import Position
from _eddy_seek.config import SeekConfig
from _eddy_seek.session import SeekSession, _sample_stdev
from _eddy_seek.strategy import CentroidStrategy, TernaryStrategy, strategy_for


class CommandError(Exception):
    """Matches ``klippy.gcode.CommandError`` for test doubles."""


class _FakeGcmd:
    error = CommandError

    def __init__(self, params: dict[str, str] | None = None) -> None:
        self._params = {k.upper(): v for k, v in (params or {}).items()}

    def get_command_parameters(self) -> dict[str, str]:
        return self._params

    def respond_info(self, msg: str) -> None:
        pass


def _test_cfg(**overrides) -> SeekConfig:
    return SeekConfig(**overrides)


class _RecordingSearchSession:
    def __init__(self) -> None:
        self.config = _test_cfg(max_iter=1, max_passes=1)
        self.positions: list[Position] = []

    def measure_at(self, offset: Position) -> float:
        self.positions.append(offset)
        return -((offset.x - 1.0) ** 2 + (offset.y + 1.0) ** 2)


def test_strategy_search_uses_positions():
    session = _RecordingSearchSession()
    best, passes_run = TernaryStrategy().search(session, _FakeGcmd())  # type: ignore[arg-type]

    assert isinstance(best, Position)
    assert passes_run == 1
    assert session.positions
    assert all(isinstance(position, Position) for position in session.positions)


def test_strategy_weights_and_runtime_set():
    cfg = _test_cfg()
    session = SeekSession.__new__(SeekSession)
    session._config = cfg

    centroid = CentroidStrategy()
    assert centroid._frequency_weight(session, 100.0, 50.0, 100.0) == 50.0
    assert centroid._frequency_weight(session, 50.0, 50.0, 100.0) == 0.0
    session._config = _test_cfg(search_for="min")
    assert centroid._frequency_weight(session, 50.0, 50.0, 100.0) == 50.0

    ternary = TernaryStrategy()
    assert ternary._is_better(session, 90.0, 80.0) is False
    assert ternary._is_better(session, 70.0, 80.0) is True
    assert _sample_stdev([1.0, 3.0], 2.0) == math.sqrt(2.0)

    cfg = _test_cfg()
    changed = cfg.apply_runtime_set(_FakeGcmd({"STRATEGY": "centroid"}))
    assert changed == ["strategy=centroid"]
    assert cfg.strategy == "centroid"
    with raises(CommandError):
        cfg.apply_runtime_set(_FakeGcmd({"STRATEGY": "bogus"}))
    with raises(CommandError, match="unknown parameter 'GRD_STEP_X'"):
        cfg.apply_runtime_set(_FakeGcmd({"GRD_STEP_X": "2.5"}))

    assert strategy_for("ternary").name == "ternary"
    assert strategy_for("centroid").name == "centroid"
    with raises(ValueError):
        strategy_for("bogus")
